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
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from uuid import UUID, uuid4

import redis

from src.core.module_cache import (
    MODULE_INDEX_KEY,
    MODULE_INDEX_GENERATION_KEY,
    MODULE_KEY_PREFIX,
    WORKSPACE_GENERATION_KEY,
    WORKSPACE_UPDATE_LOCK_SECONDS,
    WORKSPACE_UPDATING_PREFIX,
    CachedModule,
)

logger = logging.getLogger(__name__)

# TTL for cached modules (24 hours)
MODULE_CACHE_TTL = 86400

REPO_PREFIX = "_repo/"
SOLUTIONS_ROOT = "_solutions"


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
_workspace_generation_ctx = threading.local()


@dataclass(frozen=True)
class SolutionContext:
    """Active per-execution solution import root."""

    solution_id: str
    global_repo_access: bool
    runtime_storage_prefix: str | None = None
    source_hashes: dict[str, str] | None = None


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


def clear_solution_context() -> None:
    """Deactivate the solution import root (restore plain _repo/ behavior)."""
    _solution_ctx.value = None


def get_solution_context() -> SolutionContext | None:
    """Return the active solution context for this thread, or None."""
    return getattr(_solution_ctx, "value", None)


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
    current = get_workspace_generation_sync()
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
        import httpx

        url = f"{api_url}/api/sdk/modules/{path}"
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
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


def _fetch_module_index_from_api(solution_id: str | None = None) -> set[str]:
    """
    Fetch the module index via GET /api/sdk/modules-index (synchronous).

    Returns the set of known workspace module paths from the API server,
    used when the Redis index is cold.  Returns empty set on any error.
    """
    creds = _get_engine_credentials()
    if not creds:
        return set()
    creds_url, token = creds
    api_url = creds_url or os.environ.get("BIFROST_API_URL", "").rstrip("/")
    if not api_url:
        return set()

    try:
        import httpx

        if solution_id:
            resp = httpx.get(
                f"{api_url}/api/sdk/modules-index",
                headers={"Authorization": f"Bearer {token}"},
                params={"solution_id": solution_id},
                timeout=10.0,
            )
        else:
            resp = httpx.get(
                f"{api_url}/api/sdk/modules-index",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
        if resp.status_code != 200:
            return set()
        return set(resp.json().get("paths", []))
    except Exception as e:
        logger.warning(f"API module-index fetch error: {e}")
        return set()


def _fetch_requirements_from_api() -> str | None:
    """
    Fetch requirements.txt via GET /api/sdk/requirements (synchronous).

    Returns the requirements content string, or None on any error / 404.
    Used as the primary cold-cache fallback in get_requirements_sync() when
    BIFROST_S3_* are absent from the child environment (Phase 2 hardening).
    """
    creds = _get_engine_credentials()
    if not creds:
        return None
    creds_url, token = creds
    api_url = creds_url or os.environ.get("BIFROST_API_URL", "").rstrip("/")
    if not api_url:
        return None

    try:
        import httpx

        resp = httpx.get(
            f"{api_url}/api/sdk/requirements",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.warning(f"API requirements-fetch returned {resp.status_code}")
            return None

        data = resp.json()
        return data.get("content")
    except Exception as e:
        logger.warning(f"API requirements-fetch error: {e}")
        return None


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
    if storage_path.startswith(f"{SOLUTIONS_ROOT}/"):
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
    solution_module = storage_path.startswith(f"{SOLUTIONS_ROOT}/")
    expected_generation = None if solution_module else workspace_generation_for_import()
    key = f"{MODULE_KEY_PREFIX}{storage_path}"
    data = client.get(key)
    if data:
        module = json.loads(data)
        if solution_module or _cached_module_matches_generation(
            module, expected_generation or ""
        ):
            _verify_deployment_module_hash(path, module)
            return module

    api_module = _fetch_module_from_api(storage_path)
    if api_module is not None:
        if not solution_module and not _cached_module_matches_generation(
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
        _verify_deployment_module_hash(path, api_module)
        return api_module

    module = _object_storage_cached_module(
        path=path,
        storage_path=storage_path,
        expected_generation=expected_generation,
    )
    if module is None:
        return None
    _cache_sync_module(
        client,
        key=key,
        storage_path=storage_path,
        module=module,
        source="object storage",
    )
    _verify_deployment_module_hash(path, module)
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
                f"{SOLUTIONS_ROOT}/"
            ) and not _cached_module_matches_generation(module, expected_generation):
                continue
            _verify_deployment_module_hash(path, module)
            resolved[path] = module

        for path in ordered_paths:
            if path not in resolved:
                resolved[path] = get_module_sync(path)
        return resolved
    except redis.RedisError as exc:
        logger.warning("Redis error fetching module batch: %s", exc)
        return {path: get_module_sync(path) for path in ordered_paths}


def _verify_deployment_module_hash(path: str, module: CachedModule) -> None:
    ctx = get_solution_context()
    if ctx is None or not ctx.runtime_storage_prefix:
        return
    expected = (ctx.source_hashes or {}).get(path.lstrip("/"))
    if expected is None:
        raise RuntimeError(f"deployment source is absent from manifest: {path}")
    actual = str(module.get("hash") or "").removeprefix("sha256:")
    if actual != expected.removeprefix("sha256:"):
        raise RuntimeError(f"deployment import integrity mismatch: {path}")


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
