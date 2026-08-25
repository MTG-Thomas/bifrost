"""
Synchronous Redis client for import hook.

Python's import system runs synchronously - we need sync Redis access
for the MetaPathFinder to fetch modules during import.

This module provides synchronous versions of the cache functions
specifically for use in virtual_import.py's MetaPathFinder.

When a cache miss occurs, we fall back via two paths (tried in order):
1. API module-fetch endpoint (GET /api/sdk/modules/<path>) — preferred when
   BIFROST_API_URL is set and a credentials file is present.  This path does
   not require BIFROST_S3_* in the child env (Phase 2 hardening).
2. Direct S3 access via botocore — legacy fallback, only active when
   BIFROST_S3_ACCESS_KEY/SECRET_KEY are set in the environment.

Self-healing: on any successful fetch, the result is re-cached to Redis so
subsequent calls on the same worker hit the fast path.
"""

import hashlib
import json
import logging
import os
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, cast
from uuid import UUID, uuid4

import redis

from src.core.module_cache import (
    MODULE_INDEX_KEY,
    MODULE_INDEX_GENERATION_KEY,
    MODULE_KEY_PREFIX,
    MODULE_RESOLUTION_KEY_PREFIX,
    WORKSPACE_GENERATION_KEY,
    WORKSPACE_UPDATE_LOCK_SECONDS,
    WORKSPACE_UPDATING_PREFIX,
    CachedModule,
    module_resolution_cache_key,
)

logger = logging.getLogger(__name__)

# TTL for cached modules (24 hours)
MODULE_CACHE_TTL = 86400

REPO_PREFIX = "_repo/"
SOLUTIONS_ROOT = "_solutions"
WORKSPACE_RELEASES_ROOT = "_workspace_releases"


# ── Per-execution Solution import root ───────────────────────────────────────
# When a solution-managed workflow runs, module resolution must be rooted at
# _solutions/{solution_id}/ — for the entry workflow's own code AND its
# `from modules.x import y` imports — falling back to the bare _repo/ root ONLY
# when the install's global_repo_access flag is on (success-criteria §3.5).
#
# The context is thread-local: each forked worker runs a single execution on one
# thread, and the import system runs synchronously on that thread, so a
# thread-local correctly scopes the root to exactly one execution with no
# cross-execution bleed. No active context == unchanged _repo/ behavior.
_solution_ctx = threading.local()
_workspace_release_ctx = threading.local()
_workspace_generation_ctx = threading.local()


@dataclass(frozen=True)
class SolutionContext:
    """Active per-execution solution import root."""

    solution_id: str
    global_repo_access: bool
    runtime_storage_prefix: str | None = None
    source_hashes: dict[str, str] | None = None


@dataclass(frozen=True)
class WorkspaceReleaseContext:
    """Immutable Workspace source tree pinned to one execution."""

    release_id: str
    runtime_storage_prefix: str
    source_hashes: dict[str, str]


@dataclass(frozen=True)
class ModuleResolution:
    """Targeted resolver result for a logical import name."""

    kind: str
    path: str
    content: str | None = None
    hash: str = ""
    storage_path: str | None = None


class ModuleResolutionError(RuntimeError):
    """Raised when targeted module resolution cannot reach the API."""


def set_solution_context(
    solution_id: UUID | str,
    global_repo_access: bool,
    *,
    runtime_storage_prefix: str | None = None,
    source_hashes: dict[str, str] | None = None,
) -> None:
    """Activate the solution import root for the current thread/execution."""
    _solution_ctx.value = SolutionContext(
        solution_id=str(solution_id),
        global_repo_access=bool(global_repo_access),
        runtime_storage_prefix=(runtime_storage_prefix.rstrip("/") + "/")
        if runtime_storage_prefix
        else None,
        source_hashes=source_hashes,
    )
    _solution_ctx.resolution_cache = {}


def clear_solution_context() -> None:
    """Deactivate the solution import root (restore plain _repo/ behavior)."""
    _solution_ctx.value = None
    _solution_ctx.resolution_cache = {}


def get_solution_context() -> SolutionContext | None:
    """Return the active solution context for this thread, or None."""
    return getattr(_solution_ctx, "value", None)


def _get_resolution_cache() -> dict[tuple[str, str | None, bool], ModuleResolution]:
    cache = getattr(_solution_ctx, "resolution_cache", None)
    if cache is None:
        cache = {}
        _solution_ctx.resolution_cache = cache
    return cast(dict[tuple[str, str | None, bool], ModuleResolution], cache)


def _get_http_client() -> Any:
    client = getattr(_solution_ctx, "http_client", None)
    if client is None:
        import httpx

        client = httpx.Client(timeout=10.0)
        _solution_ctx.http_client = client
    return client


def _close_http_client() -> None:
    client = getattr(_solution_ctx, "http_client", None)
    if client is not None:
        with suppress(Exception):
            client.close()
        _solution_ctx.http_client = None


def set_workspace_release_context(
    release_id: str,
    *,
    runtime_storage_prefix: str,
    source_hashes: dict[str, str],
) -> None:
    """Pin all global Workspace imports to one immutable release tree."""
    normalized_prefix = runtime_storage_prefix.rstrip("/") + "/"
    if not normalized_prefix.startswith(f"{WORKSPACE_RELEASES_ROOT}/"):
        raise ValueError("workspace release runtime prefix is outside its storage root")
    release_digest = release_id.removeprefix("sha256:")
    if (
        not release_id.startswith("sha256:")
        or len(release_digest) != 64
        or any(char not in "0123456789abcdef" for char in release_digest)
        or not source_hashes
    ):
        raise ValueError("workspace release context requires an id and source manifest")
    if any(
        not isinstance(path, str)
        or not path
        or not isinstance(digest, str)
        or len(digest.removeprefix("sha256:")) != 64
        or any(
            char not in "0123456789abcdef"
            for char in digest.removeprefix("sha256:")
        )
        for path, digest in source_hashes.items()
    ):
        raise ValueError("workspace release source manifest is invalid")
    _workspace_release_ctx.value = WorkspaceReleaseContext(
        release_id=release_id,
        runtime_storage_prefix=normalized_prefix,
        source_hashes=dict(source_hashes),
    )


def clear_workspace_release_context() -> None:
    """Remove the immutable Workspace release pin for this execution."""
    _workspace_release_ctx.value = None


def get_workspace_release_context() -> WorkspaceReleaseContext | None:
    """Return the immutable Workspace release pin for this execution."""
    return getattr(_workspace_release_ctx, "value", None)


def set_workspace_generation_context(generation: str | None) -> None:
    """Pin lazy imports to the generation selected at execution start."""
    _workspace_generation_ctx.value = generation


def clear_workspace_generation_context() -> None:
    """Remove the current execution's lazy-import generation pin."""
    _workspace_generation_ctx.value = None


def get_workspace_generation_context() -> str | None:
    """Return the current execution's generation pin, if any."""
    return getattr(_workspace_generation_ctx, "value", None)


def _candidate_storage_paths(path: str) -> list[str]:
    """Ordered storage paths to try for a relative module path.

    - No active solution → just the bare path (resolved under _repo/ downstream).
    - Solution active → the solution-rooted path FIRST. When global_repo_access
      is on, the bare path follows as a fallback; when off, there is no fallback
      (a _repo/ import must NOT silently resolve — criterion 4).

    The returned paths are storage paths: a bare path is later read from the
    _repo/ prefix; a path already under ``_solutions/`` is read verbatim.
    """
    ctx = get_solution_context()
    release_ctx = get_workspace_release_context()
    if ctx is not None and release_ctx is not None:
        raise RuntimeError("solution and Workspace release import roots cannot overlap")
    if release_ctx is not None:
        return [f"{release_ctx.runtime_storage_prefix}{path.lstrip('/')}"]
    if ctx is None:
        return [path]
    root = ctx.runtime_storage_prefix or f"{SOLUTIONS_ROOT}/{ctx.solution_id}/"
    rooted = f"{root}{path.lstrip('/')}"
    if ctx.global_repo_access:
        return [rooted, path]
    return [rooted]


def candidate_module_paths(path: str) -> list[str]:
    """Storage paths that may contain a concrete module file."""
    return _candidate_storage_paths(path)


def candidate_index_prefixes(base_path: str) -> list[str]:
    """Storage-path prefixes to scan the module index with, for namespace-package
    (PEP 420) detection of ``base_path`` (e.g. "modules").

    Mirrors :func:`_candidate_storage_paths`: solution-rooted prefix first, with
    the bare prefix only when global_repo_access is on; bare prefix only when no
    solution is active. The finder tests ``index_entry.startswith(prefix)``.
    """
    base = base_path.rstrip("/")
    ctx = get_solution_context()
    release_ctx = get_workspace_release_context()
    if ctx is not None and release_ctx is not None:
        raise RuntimeError("solution and Workspace release import roots cannot overlap")
    if release_ctx is not None:
        return [f"{release_ctx.runtime_storage_prefix}{base}/"]
    if ctx is None:
        return [f"{base}/"]
    root = ctx.runtime_storage_prefix or f"{SOLUTIONS_ROOT}/{ctx.solution_id}/"
    rooted = f"{root}{base}/"
    if ctx.global_repo_access:
        return [rooted, f"{base}/"]
    return [rooted]


# Cached S3 client — reused across calls to avoid repeated setup
_s3_client: Any = None
_s3_available: bool | None = None
_blob_container_client: Any = None
_blob_available: bool | None = None


def _object_storage_provider() -> str:
    explicit_provider = os.environ.get("BIFROST_OBJECT_STORAGE_PROVIDER")
    if explicit_provider:
        return explicit_provider.lower()
    if os.environ.get("BIFROST_AZURE_BLOB_ACCOUNT_URL") and os.environ.get(
        "BIFROST_AZURE_BLOB_CONTAINER"
    ):
        return "azure_blob"
    return "s3"


@lru_cache(maxsize=1)
def _get_sync_redis() -> Any:
    """
    Get synchronous Redis client.

    Uses lru_cache to reuse connection across imports.
    """
    return redis.Redis.from_url(
        os.environ.get("BIFROST_REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


class WorkspaceGenerationChangedError(RuntimeError):
    """Raised when source activation races an execution's load boundary."""


class WorkspaceGenerationMissingError(RuntimeError):
    """Raised when an execution reaches workspace loading without a source pin."""


class WorkspaceSourceUpdatingError(RuntimeError):
    """Raised while a source transaction has closed the execution load gate."""


def _decode_ready_generation(value: str | bytes) -> str:
    """Decode a Redis generation value and reject an in-progress barrier."""
    generation = value if isinstance(value, str) else value.decode()
    if generation.startswith(WORKSPACE_UPDATING_PREFIX):
        raise WorkspaceSourceUpdatingError(
            "workspace source is being updated; execution load is deferred"
        )
    return generation


def get_workspace_generation_sync() -> str:
    """Return the shared workspace generation, initializing a cold Redis key."""
    release_ctx = get_workspace_release_context()
    if release_ctx is not None:
        return release_ctx.release_id
    try:
        client = _get_sync_redis()
        value = client.get(WORKSPACE_GENERATION_KEY)
        if value:
            return _decode_ready_generation(value)

        candidate = uuid4().hex
        if client.set(WORKSPACE_GENERATION_KEY, candidate, nx=True):
            return candidate

        value = client.get(WORKSPACE_GENERATION_KEY)
        if not value:
            raise RuntimeError(
                "workspace generation is unavailable after initialization"
            )
        return _decode_ready_generation(value)
    except redis.RedisError as exc:
        raise RuntimeError("workspace generation is unavailable") from exc


def wait_for_workspace_generation_sync(
    *,
    timeout_seconds: float = WORKSPACE_UPDATE_LOCK_SECONDS,
    poll_seconds: float = 0.05,
) -> str:
    """Wait for an in-progress source transaction to become stable."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            return get_workspace_generation_sync()
        except WorkspaceSourceUpdatingError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll_seconds)


def assert_workspace_generation(expected: str | None) -> str:
    """Fail closed when source changed after an execution began loading."""
    if not expected:
        raise WorkspaceGenerationMissingError(
            "workspace source generation was not pinned before execution loading"
        )
    release_ctx = get_workspace_release_context()
    current = (
        release_ctx.release_id
        if release_ctx is not None
        else get_workspace_generation_sync()
    )
    if current != expected:
        raise WorkspaceGenerationChangedError(
            "workspace source generation changed while the execution was loading; "
            "the stale import closure was not executed"
        )
    return current


def workspace_generation_for_import() -> str:
    """Return a safe generation stamp for a workspace import.

    Executions pin a generation before loading their entry workflow. Every lazy
    import revalidates that pin, preventing a workflow that began on one source
    revision from importing a helper activated later in the same run.
    """
    expected = get_workspace_generation_context()
    if expected:
        return assert_workspace_generation(expected)
    return get_workspace_generation_sync()


def _get_engine_credentials() -> tuple[str, str] | None:
    """
    Read the engine bearer token and API URL from the credentials file.

    The file is written by save_credentials() (either from the handed-down
    context_data["engine_token"] path or the legacy authenticate_engine()
    path) and carries both the access token and the API URL.  Returning the
    URL from here means the API module-fetch fallback works even when
    BIFROST_API_URL is not set in the child env (it is not, in either the
    test stack or the k8s worker manifests).

    Returns (api_url, access_token) or None if unavailable.
    """
    try:
        from bifrost.credentials import get_credentials

        creds = get_credentials()
        if creds and creds.get("access_token") and creds.get("api_url"):
            return creds["api_url"].rstrip("/"), creds["access_token"]
    except Exception:
        # Credentials file absent/unreadable in this child — caller falls back
        # to BIFROST_API_URL or treats the cold-cache fetch as unavailable.
        pass
    return None


def _fetch_module_from_api(path: str) -> CachedModule | None:
    """
    Fetch a module via GET /api/sdk/modules/<path> (synchronous, httpx).

    Uses the engine bearer token and API URL from the credentials file,
    falling back to the BIFROST_API_URL env var only if the creds file has
    no URL.  Returns a CachedModule dict on success, None on any error
    (404, auth failure, etc.).

    This is the primary cold-cache fallback when BIFROST_S3_* are absent
    from the child environment (Phase 2 hardening).
    """
    creds = _get_engine_credentials()
    if not creds:
        return None
    creds_url, token = creds

    # Creds-file URL is the source of truth; env var is a secondary source.
    api_url = creds_url or os.environ.get("BIFROST_API_URL", "").rstrip("/")
    if not api_url:
        return None

    try:
        url = f"{api_url}/api/sdk/modules/{path}"
        resp = _get_http_client().get(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.warning(f"API module-fetch returned {resp.status_code} for {path}")
            return None

        data: CachedModule = resp.json()
        return data
    except Exception as e:
        logger.warning(f"API module-fetch error for {path}: {e}")
        return None


def _fetch_module_resolution_from_api(name: str) -> ModuleResolution | None:
    """
    Resolve one import name via GET /api/sdk/modules-resolve.

    This is the targeted replacement for fetching the whole module index during
    child import resolution. The API owns Redis→S3 module lookup and bounded
    namespace prefix probing; the child memoizes each result for the active
    execution.
    """
    creds = _get_engine_credentials()
    if not creds:
        return None
    creds_url, token = creds
    api_url = creds_url or os.environ.get("BIFROST_API_URL", "").rstrip("/")
    if not api_url:
        return None

    ctx = get_solution_context()
    params: dict[str, object] = {"name": name}
    if ctx is not None:
        params["solution_id"] = ctx.solution_id
        params["global_repo_access"] = ctx.global_repo_access

    try:
        resp = _get_http_client().get(
            f"{api_url}/api/sdk/modules-resolve",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        if resp.status_code != 200:
            logger.warning(
                f"API module-resolve returned {resp.status_code} for {name}"
            )
            return None

        data = resp.json()
        kind = data.get("kind")
        if kind not in {"module", "package", "namespace", "not_found"}:
            logger.warning(f"API module-resolve returned invalid kind for {name}")
            return None
        return ModuleResolution(
            kind=kind,
            path=data.get("path") or name.replace(".", "/"),
            content=data.get("content"),
            hash=data.get("hash") or "",
            storage_path=data.get("storage_path"),
        )
    except Exception as e:
        logger.warning(f"API module-resolve error for {name}: {e}")
        return None


def _resolution_redis_key(name: str) -> str:
    ctx = get_solution_context()
    cache_key = module_resolution_cache_key(
        name,
        solution_id=ctx.solution_id if ctx is not None else None,
        global_repo_access=ctx.global_repo_access if ctx is not None else False,
    )
    return f"{MODULE_RESOLUTION_KEY_PREFIX}{cache_key}"


def _module_resolution_from_cached_source(
    *,
    kind: str,
    path: str,
    storage_path: str,
    raw_module: str,
) -> ModuleResolution | None:
    try:
        module = json.loads(raw_module)
        content = module["content"]
        content_hash = module.get("hash") or ""
        if not isinstance(content, str) or not isinstance(content_hash, str):
            return None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    return ModuleResolution(
        kind=kind,
        path=path,
        content=content,
        hash=content_hash,
        storage_path=storage_path,
    )


def _get_cached_module_resolution(name: str) -> ModuleResolution | None:
    """Hydrate resolver metadata and source directly from worker-visible Redis."""
    try:
        client = _get_sync_redis()
        raw_resolution = client.get(_resolution_redis_key(name))
        if not raw_resolution:
            return None
        data = json.loads(raw_resolution)
        kind = data.get("kind")
        path = data.get("path") or name.replace(".", "/")
        if kind in {"namespace", "not_found"}:
            return ModuleResolution(kind=kind, path=path)
        if kind not in {"module", "package"}:
            return None
        storage_path = data.get("storage_path")
        if not isinstance(storage_path, str):
            return None
        raw_module = client.get(f"{MODULE_KEY_PREFIX}{storage_path}")
        if not raw_module:
            return None
        return _module_resolution_from_cached_source(
            kind=kind,
            path=path,
            storage_path=storage_path,
            raw_module=raw_module,
        )
    except (redis.RedisError, json.JSONDecodeError, TypeError) as exc:
        logger.debug("Cached module resolution unavailable for %s: %s", name, exc)
        return None


def _get_exact_scoped_module(name: str) -> ModuleResolution | None:
    """Resolve a concrete module in the primary scope without an HTTP hop.

    This intentionally does not probe namespaces or a Solution's optional
    workspace fallback. Those cases require the API resolver to preserve the
    rule that a Solution namespace shadows a concrete workspace module.
    """
    base_path = name.replace(".", "/")
    ctx = get_solution_context()
    scope_prefix = f"{SOLUTIONS_ROOT}/{ctx.solution_id}/" if ctx else ""
    try:
        client = _get_sync_redis()
        for relative_path, kind in (
            (f"{base_path}.py", "module"),
            (f"{base_path}/__init__.py", "package"),
        ):
            storage_path = f"{scope_prefix}{relative_path}"
            raw_module = client.get(f"{MODULE_KEY_PREFIX}{storage_path}")
            if not raw_module:
                continue
            return _module_resolution_from_cached_source(
                kind=kind,
                path=relative_path,
                storage_path=storage_path,
                raw_module=raw_module,
            )
    except redis.RedisError as exc:
        logger.debug("Direct module cache unavailable for %s: %s", name, exc)
    return None


def resolve_module_sync(name: str) -> ModuleResolution:
    """Resolve one logical import name, cached for the active execution."""
    top_level = name.split(".", 1)[0]
    if (
        top_level in sys.builtin_module_names
        or top_level in getattr(sys, "stdlib_module_names", set())
    ):
        return ModuleResolution(kind="not_found", path=name.replace(".", "/"))

    ctx = get_solution_context()
    cache_key = (
        name,
        ctx.solution_id if ctx is not None else None,
        ctx.global_repo_access if ctx is not None else False,
    )
    cache = _get_resolution_cache()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    resolution = _get_cached_module_resolution(name)
    if resolution is None:
        resolution = _get_exact_scoped_module(name)
    if resolution is None:
        resolution = _fetch_module_resolution_from_api(name)
    if resolution is None:
        raise ModuleResolutionError(f"Module resolver unavailable for {name}")

    cache[cache_key] = resolution
    return resolution


def _fetch_module_index_from_api(solution_id: str | None = None) -> set[str]:
    """Fetch the module index used by the fork's release-aware cache paths."""
    creds = _get_engine_credentials()
    if not creds:
        return set()
    creds_url, token = creds
    api_url = creds_url or os.environ.get("BIFROST_API_URL", "").rstrip("/")
    if not api_url:
        return set()

    try:
        params = {"solution_id": solution_id} if solution_id else None
        resp = _get_http_client().get(
            f"{api_url}/api/sdk/modules-index",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        if resp.status_code != 200:
            return set()
        return set(resp.json().get("paths", []))
    except Exception as exc:
        logger.warning("API module-index fetch error: %s", exc)
        return set()


def _fetch_requirements_from_api() -> tuple[bool, str | None]:
    """
    Fetch requirements.txt via GET /api/sdk/requirements (synchronous).

    Returns ``(authoritative, content)``. A 404 is authoritative absence, while
    connection/auth/server failures return ``(False, None)`` so the caller can
    distinguish them from a workspace that intentionally has no requirements.
    Used as the primary cold-cache fallback in get_requirements_sync() when
    BIFROST_S3_* are absent from the child environment (Phase 2 hardening).
    """
    creds = _get_engine_credentials()
    if not creds:
        return False, None
    creds_url, token = creds
    api_url = creds_url or os.environ.get("BIFROST_API_URL", "").rstrip("/")
    if not api_url:
        return False, None

    try:
        resp = _get_http_client().get(
            f"{api_url}/api/sdk/requirements",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 404:
            return True, None
        if resp.status_code != 200:
            logger.warning(f"API requirements-fetch returned {resp.status_code}")
            return False, None

        data = resp.json()
        return True, data.get("content")
    except Exception as e:
        logger.warning(f"API requirements-fetch error: {e}")
        return False, None


def _get_s3_client() -> Any:
    """
    Get or create a sync S3 client using botocore (always available via aiobotocore).
    """
    global _s3_client, _s3_available

    if _object_storage_provider() != "s3":
        _s3_available = False
        return None

    if _s3_available is False:
        return None

    if _s3_client is not None:
        return _s3_client

    endpoint_url = os.environ.get("BIFROST_S3_ENDPOINT_URL")
    access_key = os.environ.get("BIFROST_S3_ACCESS_KEY")
    secret_key = os.environ.get("BIFROST_S3_SECRET_KEY")
    region = os.environ.get("BIFROST_S3_REGION", "us-east-1")

    if not all([access_key, secret_key]):
        logger.debug("S3 not configured, skipping S3 fallback")
        _s3_available = False
        return None

    try:
        import botocore.session  # type: ignore[import-untyped]

        session = botocore.session.get_session()
        client = session.create_client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        _s3_client = client
        _s3_available = True
        return client
    except Exception:
        _s3_available = False
        logger.debug("botocore not available, S3 fallback disabled")
        return None


def _storage_path_to_s3_key(storage_path: str) -> str:
    """Map a storage path to its S3 key.

    A bare relative path lives under the _repo/ prefix; a path already rooted at
    ``_solutions/`` is used verbatim (it already carries its full prefix).
    """
    if storage_path.startswith(
        (f"{SOLUTIONS_ROOT}/", f"{WORKSPACE_RELEASES_ROOT}/")
    ):
        return storage_path
    return f"{REPO_PREFIX}{storage_path}"


def _get_s3_module(storage_path: str) -> bytes | None:
    """
    Fetch a module from S3 by storage path (synchronous).

    Bare paths resolve under _repo/; ``_solutions/{id}/...`` paths are used
    verbatim. Uses botocore sync client since this runs in worker subprocesses.
    Returns raw bytes or None if not found.
    """
    bucket = os.environ.get("BIFROST_S3_BUCKET")
    if not bucket:
        return None

    client = _get_s3_client()
    if client is None:
        return None

    try:
        key = _storage_path_to_s3_key(storage_path)
        response = client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    except Exception as e:
        # Check for S3 NoSuchKey specifically
        resp = getattr(e, "response", None)
        if isinstance(resp, dict):
            code = resp.get("Error", {}).get("Code", "")
            if code == "NoSuchKey":
                logger.debug(f"Module not found in S3: {storage_path}")
                return None
        logger.warning(f"S3 fallback error for {storage_path}: {e}")
        return None


def _get_blob_container_client() -> Any:
    """Get or create a sync Azure Blob container client."""
    global _blob_container_client, _blob_available

    if _blob_available is False:
        return None

    if _blob_container_client is not None:
        return _blob_container_client

    if _object_storage_provider() != "azure_blob":
        _blob_available = False
        return None

    account_url = os.environ.get("BIFROST_AZURE_BLOB_ACCOUNT_URL")
    container = os.environ.get("BIFROST_AZURE_BLOB_CONTAINER")
    auth = os.environ.get("BIFROST_AZURE_BLOB_AUTH", "default_credential")
    account_key = os.environ.get("BIFROST_AZURE_BLOB_ACCOUNT_KEY")

    if not account_url or not container:
        logger.debug("Azure Blob not configured, skipping Blob fallback")
        _blob_available = False
        return None

    try:
        from azure.storage.blob import BlobServiceClient

        credential: Any
        if auth == "account_key":
            if not account_key:
                logger.debug("Azure Blob account key missing, skipping Blob fallback")
                _blob_available = False
                return None
            credential = account_key
        elif auth == "default_credential":
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        else:
            logger.warning(f"Unsupported Azure Blob auth mode: {auth}")
            _blob_available = False
            return None

        service_client = BlobServiceClient(account_url, credential=credential)
        _blob_container_client = service_client.get_container_client(container)
        _blob_available = True
        return _blob_container_client
    except Exception as e:
        _blob_available = False
        logger.warning(f"Azure Blob fallback unavailable: {e}")
        return None


def _get_blob_module(storage_path: str) -> bytes | None:
    """Fetch a module from Azure Blob by storage path."""
    client = _get_blob_container_client()
    if client is None:
        return None

    key = _storage_path_to_s3_key(storage_path)
    try:
        return client.download_blob(key).readall()
    except Exception as e:
        error_code = getattr(e, "error_code", "")
        if error_code == "BlobNotFound":
            logger.debug(f"Module not found in Azure Blob: {storage_path}")
            return None
        logger.warning(f"Azure Blob fallback error for {storage_path}: {e}")
        return None


def _get_object_storage_module(storage_path: str) -> bytes | None:
    if _object_storage_provider() == "azure_blob":
        return _get_blob_module(storage_path)
    return _get_s3_module(storage_path)


def _require_workspace_generation(
    expected_generation: str | None, *, message: str
) -> None:
    if (
        expected_generation is not None
        and workspace_generation_for_import() != expected_generation
    ):
        raise WorkspaceGenerationChangedError(message)


def _cache_sync_module(
    client: Any,
    *,
    key: str,
    storage_path: str,
    module: CachedModule,
    source: str,
) -> None:
    try:
        client.setex(key, MODULE_CACHE_TTL, json.dumps(module))
        client.sadd(MODULE_INDEX_KEY, storage_path)
    except redis.RedisError as exc:
        logger.warning(f"Failed to re-cache {source} module to Redis: {exc}")


def _object_storage_cached_module(
    *, path: str, storage_path: str, expected_generation: str | None
) -> CachedModule | None:
    storage_content = _get_object_storage_module(storage_path)
    if storage_content is None:
        return None
    try:
        content_text = storage_content.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning(
            f"Could not decode object storage module as UTF-8: {storage_path}"
        )
        return None
    module: CachedModule = {
        "content": content_text,
        "path": path,
        "hash": hashlib.sha256(storage_content).hexdigest(),
    }
    if expected_generation:
        module["generation"] = expected_generation
    _require_workspace_generation(
        expected_generation,
        message="workspace source generation changed during module fallback",
    )
    return module


def _resolve_module_candidate(
    client: Any, *, path: str, storage_path: str
) -> CachedModule | None:
    immutable_module = storage_path.startswith(
        (f"{SOLUTIONS_ROOT}/", f"{WORKSPACE_RELEASES_ROOT}/")
    )
    integrity_error: RuntimeError | None = None
    expected_generation = (
        None if immutable_module else workspace_generation_for_import()
    )
    key = f"{MODULE_KEY_PREFIX}{storage_path}"
    data = client.get(key)
    if data:
        try:
            module = json.loads(data)
        except (TypeError, json.JSONDecodeError):
            module = None
        if immutable_module and module is not None:
            try:
                _verify_immutable_module_hash(path, module)
            except RuntimeError as exc:
                integrity_error = exc
                client.delete(key)
            else:
                return module
        elif module is not None and _cached_module_matches_generation(
            module, expected_generation or ""
        ):
            return module

    api_module = _fetch_module_from_api(storage_path)
    if api_module is not None:
        if not immutable_module and not _cached_module_matches_generation(
            api_module, expected_generation or ""
        ):
            raise WorkspaceGenerationChangedError(
                "module API returned a different workspace generation"
            )
        _cache_sync_module(
            client,
            key=key,
            storage_path=storage_path,
            module=api_module,
            source="API",
        )
        _verify_immutable_module_hash(path, api_module)
        return api_module

    module = _object_storage_cached_module(
        path=path,
        storage_path=storage_path,
        expected_generation=expected_generation,
    )
    if module is None:
        if integrity_error is not None:
            raise integrity_error
        return None
    _cache_sync_module(
        client,
        key=key,
        storage_path=storage_path,
        module=module,
        source="object storage",
    )
    _verify_immutable_module_hash(path, module)
    _require_workspace_generation(
        expected_generation,
        message="workspace source generation changed during module fallback",
    )
    return module


def get_module_sync(path: str) -> CachedModule | None:
    """
    Fetch a single module from cache (synchronous).

    Called by VirtualModuleFinder.find_spec() during import resolution AND by
    the worker to load the entry workflow's own code.

    When a Solution context is active (set_solution_context), candidate storage
    paths are tried in order — solution-rooted first, then bare _repo/ only when
    global_repo_access is on. With no context, behavior is unchanged: the bare
    path resolves under _repo/.

    Per candidate, the lookup order is:
    1. Redis cache (fast path)
    2. API endpoint GET /api/sdk/modules/<storage_path>  — preferred cold-cache
       fallback (no S3 env vars required; uses engine token from credentials
       file; the server performs the Redis→S3 lookup)
    3. Direct S3 via botocore — legacy fallback when BIFROST_S3_* are present
    Then the next candidate; None if no candidate resolves.

    On any successful fallback hit the module is re-cached to Redis (under the
    storage-path key) so the next call on the same worker takes the fast path.
    The returned CachedModule keeps the logical (bare) ``path`` so __file__ and
    spec origin stay stable regardless of where the bytes were stored.
    """
    try:
        client = _get_sync_redis()

        for storage_path in _candidate_storage_paths(path):
            module = _resolve_module_candidate(
                client, path=path, storage_path=storage_path
            )
            if module is not None:
                return module

        logger.debug(f"Module not in cache, API, or object storage: {path}")
        return None

    except redis.RedisError as e:
        logger.warning(f"Redis error fetching module {path}: {e}")
        return None


def get_modules_sync(paths: list[str]) -> dict[str, CachedModule | None]:
    """Fetch several modules with one Redis read, preserving cold-cache fallbacks."""
    ordered_paths = list(dict.fromkeys(paths))
    if not ordered_paths:
        return {}

    try:
        client = _get_sync_redis()
        candidates = [
            (path, storage_path)
            for path in ordered_paths
            for storage_path in _candidate_storage_paths(path)
        ]
        values = client.mget(
            [f"{MODULE_KEY_PREFIX}{storage_path}" for _path, storage_path in candidates]
        )
        resolved: dict[str, CachedModule | None] = {}
        expected_generation = workspace_generation_for_import()
        for (path, storage_path), data in zip(candidates, values, strict=True):
            if path in resolved or not data:
                continue
            module = json.loads(data)
            if not storage_path.startswith(
                (f"{SOLUTIONS_ROOT}/", f"{WORKSPACE_RELEASES_ROOT}/")
            ) and not _cached_module_matches_generation(module, expected_generation):
                continue
            _verify_immutable_module_hash(path, module)
            resolved[path] = module

        for path in ordered_paths:
            if path not in resolved:
                resolved[path] = get_module_sync(path)
        return resolved
    except redis.RedisError as exc:
        logger.warning("Redis error fetching module batch: %s", exc)
        return {path: get_module_sync(path) for path in ordered_paths}


def _verify_immutable_module_hash(path: str, module: CachedModule) -> None:
    ctx = get_solution_context()
    release_ctx = get_workspace_release_context()
    if ctx is not None and release_ctx is not None:
        raise RuntimeError("solution and Workspace release import roots cannot overlap")
    if release_ctx is not None:
        expected = release_ctx.source_hashes.get(path.lstrip("/"))
        label = "workspace release"
    elif ctx is not None and ctx.runtime_storage_prefix:
        expected = (ctx.source_hashes or {}).get(path.lstrip("/"))
        label = "deployment"
    else:
        return
    if expected is None:
        raise RuntimeError(f"{label} source is absent from manifest: {path}")
    content = module.get("content")
    labelled = str(module.get("hash") or "").removeprefix("sha256:")
    actual = (
        hashlib.sha256(content.encode("utf-8")).hexdigest()
        if isinstance(content, str)
        else ""
    )
    if actual != labelled or actual != expected.removeprefix("sha256:"):
        raise RuntimeError(f"{label} import integrity mismatch: {path}")


def _cached_module_matches_generation(module: object, generation: str) -> bool:
    if not isinstance(module, dict):
        return False
    content = module.get("content")
    content_hash = module.get("hash")
    return (
        isinstance(content, str)
        and isinstance(content_hash, str)
        and module.get("generation") == generation
        and hashlib.sha256(content.encode("utf-8")).hexdigest() == content_hash
    )


def _list_s3_modules() -> set[str]:
    """
    List all Python module paths in S3 _repo/ (synchronous).

    Used as a fallback when the Redis module index is empty, which can happen
    after Redis restarts or cache eviction. Returns paths relative to _repo/
    (e.g. "features/spotify_journal/services/spotify_api.py").
    """
    bucket = os.environ.get("BIFROST_S3_BUCKET")
    if not bucket:
        return set()

    client = _get_s3_client()
    if client is None:
        return set()

    paths: set[str] = set()
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=REPO_PREFIX):
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                if key.endswith(".py"):
                    # Strip the _repo/ prefix to get the relative path
                    paths.add(key[len(REPO_PREFIX) :])
    except Exception as e:
        logger.warning(f"S3 list error when rebuilding module index: {e}")

    return paths


def _list_blob_modules() -> set[str]:
    """List all Python module paths in Azure Blob _repo/ (synchronous)."""
    client = _get_blob_container_client()
    if client is None:
        return set()

    paths: set[str] = set()
    try:
        for blob in client.list_blobs(name_starts_with=REPO_PREFIX):
            key: str = blob.name
            if key.endswith(".py"):
                paths.add(key[len(REPO_PREFIX) :])
    except Exception as e:
        logger.warning(f"Azure Blob list error when rebuilding module index: {e}")

    return paths


def _list_object_storage_modules() -> set[str]:
    if _object_storage_provider() == "azure_blob":
        return _list_blob_modules()
    return _list_s3_modules()


def get_module_index_sync() -> set[str]:
    """
    Get all cached module paths (synchronous).

    When the Redis index is empty (cold cache after restart or eviction),
    falls back via two paths (tried in order):
    1. API endpoint GET /api/sdk/modules-index — preferred (no S3 env needed)
    2. Direct S3 listing — legacy fallback when BIFROST_S3_* are present

    On any successful fallback hit, Redis is repopulated so subsequent calls
    take the fast path.
    """
    release_ctx = get_workspace_release_context()
    if release_ctx is not None:
        # The immutable manifest is authoritative. Never merge a mutable Redis
        # or _repo listing into a release-pinned execution.
        return {
            f"{release_ctx.runtime_storage_prefix}{path.lstrip('/')}"
            for path in release_ctx.source_hashes
            if path.endswith(".py")
        }

    try:
        client = _get_sync_redis()
        generation = workspace_generation_for_import()
        index_generation = client.get(MODULE_INDEX_GENERATION_KEY)
        if isinstance(index_generation, bytes):
            index_generation = index_generation.decode()
        paths = client.smembers(MODULE_INDEX_KEY)
        if paths and index_generation == generation:
            return {p if isinstance(p, str) else p.decode() for p in paths}

        # Redis index is empty — try API first
        logger.debug("Module index empty in Redis, falling back to API listing")
        ctx = get_solution_context()
        api_paths = (
            _fetch_module_index_from_api(solution_id=ctx.solution_id)
            if ctx is not None
            else _fetch_module_index_from_api()
        )
        if api_paths:
            try:
                client.sadd(MODULE_INDEX_KEY, *api_paths)
                client.expire(MODULE_INDEX_KEY, MODULE_CACHE_TTL)
                client.set(MODULE_INDEX_GENERATION_KEY, generation)
            except redis.RedisError as e:
                logger.warning(f"Failed to repopulate module index from API: {e}")
            return api_paths

        # API not available — try direct object storage (legacy path)
        logger.debug("API index unavailable, falling back to object storage listing")
        storage_paths = _list_object_storage_modules()
        if storage_paths:
            try:
                client.sadd(MODULE_INDEX_KEY, *storage_paths)
                client.expire(MODULE_INDEX_KEY, MODULE_CACHE_TTL)
                client.set(MODULE_INDEX_GENERATION_KEY, generation)
            except redis.RedisError as e:
                logger.warning(f"Failed to repopulate module index in Redis: {e}")
            return storage_paths

        return set()
    except redis.RedisError as e:
        logger.warning(f"Redis error fetching module index: {e}")
        return set()


def solution_has_submodules(base_path: str) -> bool:
    """True if the active solution has any module under ``{base_path}/``.

    Namespace-package (PEP 420) detection for solution code can't rely on the
    Redis module index alone: a freshly-deployed module is only indexed once
    it's first loaded, but it can't load until its parent package resolves as a
    namespace — a chicken-and-egg. So when a solution is active we check the
    API-backed module index under ``_solutions/{id}/{base_path}/`` first, then
    direct S3 as the legacy fallback when the child still has S3 credentials.

    Returns False when no solution is active (the _repo/ index path already handles
    that case) or neither fallback can prove a submodule exists.
    """
    ctx = get_solution_context()
    if ctx is None:
        return False
    root = ctx.runtime_storage_prefix or f"{SOLUTIONS_ROOT}/{ctx.solution_id}/"
    prefix = f"{root}{base_path.rstrip('/')}/"
    api_paths = _fetch_module_index_from_api(solution_id=ctx.solution_id)
    if any(path.startswith(prefix) for path in api_paths):
        return True

    if _object_storage_provider() == "azure_blob":
        client = _get_blob_container_client()
        if client is None:
            return False
        try:
            return next(client.list_blobs(name_starts_with=prefix), None) is not None
        except Exception as e:
            logger.debug(f"Azure Blob submodule check failed for {prefix}: {e}")
            return False
    bucket = os.environ.get("BIFROST_S3_BUCKET")
    if not bucket:
        return False
    client = _get_s3_client()
    if client is None:
        return False
    try:
        resp = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
        return resp.get("KeyCount", 0) > 0 or bool(resp.get("Contents"))
    except Exception as e:
        logger.debug(f"S3 submodule check failed for {prefix}: {e}")
        return False


def reset_sync_redis() -> None:
    """Reset the sync Redis client."""
    _get_sync_redis.cache_clear()


def reset_s3_client() -> None:
    """Reset the cached S3 client. Used for testing."""
    global _s3_client, _s3_available
    _s3_client = None
    _s3_available = None


def reset_blob_client() -> None:
    """Reset the cached Azure Blob client. Used for testing."""
    global _blob_container_client, _blob_available
    _blob_container_client = None
    _blob_available = None
