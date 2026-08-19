"""
Async Redis client for module caching.

Used by API services and background jobs that have async context.
Workers read modules from this cache during virtual imports.

Key patterns:
- bifrost:module:{path} - JSON: {content, path, hash}
- bifrost:module:index - SET of all module paths
"""

import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import AsyncIterator, Awaitable, NotRequired, TypedDict, cast
from uuid import uuid4

from src.core.log_safety import log_safe
from src.core.redis_client import get_redis_client
from src.services.repo_storage import RepoStorage

logger = logging.getLogger(__name__)

MODULE_KEY_PREFIX = "bifrost:module:"
MODULE_INDEX_KEY = "bifrost:module:index"
MODULE_INDEX_GENERATION_KEY = "bifrost:module:index:generation"
WORKSPACE_GENERATION_KEY = "bifrost:workspace:generation"
WORKSPACE_GENERATION_CHANNEL = "bifrost:workspace:generation:events"
WORKSPACE_UPDATING_PREFIX = "updating:"
WORKSPACE_UPDATE_LOCK_KEY = "bifrost:workspace:generation:update-lock"
WORKSPACE_UPDATE_LOCK_SECONDS = 300
WORKSPACE_UPDATE_LOCK_WAIT_SECONDS = 10
WORKSPACE_UPDATE_LOCK_RENEW_SECONDS = WORKSPACE_UPDATE_LOCK_SECONDS / 3
SOLUTIONS_ROOT = "_solutions"
WORKSPACE_RELEASES_ROOT = "_workspace_releases"
_workspace_update_depth: ContextVar[int] = ContextVar(
    "workspace_update_depth", default=0
)


class CachedModule(TypedDict):
    """Schema for cached module data."""

    content: str
    path: str
    hash: str
    generation: NotRequired[str]


class WorkspacePropagationError(RuntimeError):
    """Durable workspace bytes changed but runtime cache proof did not converge."""


@dataclass(frozen=True)
class ModuleCoherence:
    """Durable/runtime evidence for one global workspace Python path."""

    path: str
    durable_sha256: str
    cache_sha256: str | None
    cache_generation: str | None
    workspace_generation: str
    indexed: bool
    coherent: bool
    state: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _ready_generation_value(value: str) -> str:
    return value.removeprefix(WORKSPACE_UPDATING_PREFIX)


def _cached_module_is_current(module: object, generation: str) -> bool:
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


def _cached_module_content_matches_hash(module: object) -> bool:
    """Verify immutable cache bytes instead of trusting their hash label."""
    if not isinstance(module, dict):
        return False
    content = module.get("content")
    content_hash = module.get("hash")
    return (
        isinstance(content, str)
        and isinstance(content_hash, str)
        and hashlib.sha256(content.encode("utf-8")).hexdigest()
        == content_hash.removeprefix("sha256:")
    )


def _decode_cached_module(data: object) -> dict[str, object] | None:
    if not data:
        return None
    try:
        decoded = json.loads(data)
    except (TypeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _module_from_bytes(
    *, path: str, content: bytes, generation: str | None
) -> CachedModule | None:
    try:
        content_text = content.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning(f"Could not decode {log_safe(path)} as UTF-8, skipping")
        return None
    return CachedModule(
        content=content_text,
        path=path,
        hash=hashlib.sha256(content).hexdigest(),
        **({"generation": generation} if generation else {}),
    )


async def get_workspace_generation() -> str:
    """Return the current workspace generation, creating one when Redis is cold.

    A random token is used instead of a counter so a Redis restart cannot
    accidentally recreate a generation value inherited by a long-lived worker
    template. ``SET NX`` makes concurrent cold starts converge on one token.
    """
    redis = get_redis_client()
    redis_conn = await redis._get_redis()
    value = await redis_conn.get(WORKSPACE_GENERATION_KEY)
    if value:
        return value if isinstance(value, str) else value.decode()

    candidate = uuid4().hex
    created = await redis_conn.set(WORKSPACE_GENERATION_KEY, candidate, nx=True)
    if created:
        return candidate

    value = await redis_conn.get(WORKSPACE_GENERATION_KEY)
    if not value:
        raise RuntimeError("workspace generation is unavailable after initialization")
    return value if isinstance(value, str) else value.decode()


async def rotate_workspace_generation(
    *,
    reason: str,
    changed_paths: list[str],
    broadcast: bool = False,
    generation: str | None = None,
) -> str:
    """Replace the workspace generation after Python source becomes active.

    Redis is the worker module-cache authority. Updating the generation after
    source/cache writes gives every execution a cheap coherence check. Release
    activation also broadcasts the new generation so worker templates recycle
    promptly; the per-execution check remains authoritative if pub/sub is lost.
    """
    redis = get_redis_client()
    redis_conn = await redis._get_redis()
    generation = generation or uuid4().hex
    await redis_conn.set(WORKSPACE_GENERATION_KEY, generation)
    await redis_conn.set(MODULE_INDEX_GENERATION_KEY, generation)

    if broadcast:
        event = {
            "action": "workspace_generation_changed",
            "generation": generation,
            "reason": reason,
            "changed_paths": sorted(set(changed_paths)),
        }
        try:
            await redis_conn.publish(WORKSPACE_GENERATION_CHANNEL, json.dumps(event))
        except Exception as exc:  # noqa: BLE001 - execution-side check is authoritative
            logger.warning(
                "Workspace generation %s activated but fleet broadcast failed: %s",
                generation,
                exc,
            )

    logger.info(
        "Workspace generation rotated to %s (%s, %s Python path(s))",
        generation,
        reason,
        len(set(changed_paths)),
    )
    return generation


async def mark_workspace_generation_updating(
    *, reason: str, changed_paths: list[str]
) -> str:
    """Close the execution load gate before a Python source mutation begins."""
    redis = get_redis_client()
    redis_conn = await redis._get_redis()
    generation = f"{WORKSPACE_UPDATING_PREFIX}{uuid4().hex}"
    await redis_conn.set(
        WORKSPACE_GENERATION_KEY,
        generation,
        ex=WORKSPACE_UPDATE_LOCK_SECONDS,
    )
    logger.info(
        "Workspace generation marked updating (%s, %s Python path(s))",
        reason,
        len(set(changed_paths)),
    )
    return generation


def _decode_redis_text(value: object) -> str | None:
    if isinstance(value, bytes):
        return value.decode()
    return value if isinstance(value, str) else None


async def _renew_workspace_update_lease(
    *, lock, redis_conn, updating_generation: str, lease_lost: asyncio.Event
) -> None:
    """Keep both the writer lease and execution barrier alive.

    ``reacquire`` is token-fenced by redis-py: it fails once the lease expires
    or another writer owns the lock.  The generation marker is renewed only
    while that same writer still owns the lease.
    """
    try:
        while True:
            await asyncio.sleep(WORKSPACE_UPDATE_LOCK_RENEW_SECONDS)
            try:
                if not await lock.reacquire():
                    lease_lost.set()
                    return
                current = _decode_redis_text(
                    await redis_conn.get(WORKSPACE_GENERATION_KEY)
                )
                if current != updating_generation or not await redis_conn.expire(
                    WORKSPACE_GENERATION_KEY, WORKSPACE_UPDATE_LOCK_SECONDS
                ):
                    lease_lost.set()
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - loss must fail closed
                lease_lost.set()
                logger.exception("Workspace update lease renewal failed: %s", exc)
                return
    except asyncio.CancelledError:
        raise


async def _workspace_update_lease_is_current(
    *, lock, redis_conn, updating_generation: str
) -> bool:
    """Fence the final generation rotation to the current writer token."""
    try:
        if not await lock.reacquire():
            return False
        current = _decode_redis_text(await redis_conn.get(WORKSPACE_GENERATION_KEY))
        return current == updating_generation
    except Exception as exc:  # noqa: BLE001 - ownership uncertainty fails closed
        logger.exception("Workspace update lease ownership check failed: %s", exc)
        return False


async def _finish_workspace_source_update(
    *,
    depth: int,
    lock,
    redis_conn,
    updating_generation: str | None,
    renewal_task: asyncio.Task[None] | None,
    lease_lost: asyncio.Event,
    reason: str,
    python_paths: list[str],
    broadcast: bool,
) -> bool:
    """Stop renewal and rotate only for the still-current writer.

    Returns ``True`` when a top-level writer lost its lease and the ready
    generation was deliberately not published.
    """
    if renewal_task is not None:
        renewal_task.cancel()
        with suppress(asyncio.CancelledError):
            await renewal_task
    if depth != 0 or updating_generation is None or lock is None or redis_conn is None:
        return False

    owns_current_lease = (
        not lease_lost.is_set()
        and await _workspace_update_lease_is_current(
            lock=lock,
            redis_conn=redis_conn,
            updating_generation=updating_generation,
        )
    )
    if not owns_current_lease:
        logger.error("Workspace update lease was lost; generation rotation suppressed")
        return True

    await rotate_workspace_generation(
        reason=reason,
        changed_paths=python_paths,
        broadcast=broadcast,
        generation=_ready_generation_value(updating_generation),
    )
    await reconcile_module_coherence(python_paths)
    return False


@asynccontextmanager
async def workspace_source_update(
    *, reason: str, changed_paths: list[str], broadcast: bool = False
) -> AsyncIterator[None]:
    """Bracket a Python mutation with an execution-safe generation barrier.

    Nested file operations share the outer barrier so a transactional multi-file
    activation exposes no intermediate ready generation.  The exit rotation also
    runs after compensation on failure, allowing executions to resume only once
    storage/cache state is stable again.
    """
    python_paths = sorted({path for path in changed_paths if path.endswith(".py")})
    if not python_paths:
        yield
        return

    depth = _workspace_update_depth.get()
    token = _workspace_update_depth.set(depth + 1)
    lock = None
    lock_acquired = False
    redis_conn = None
    updating_generation: str | None = None
    renewal_task: asyncio.Task[None] | None = None
    lease_lost = asyncio.Event()
    body_succeeded = False
    lost_before_rotation = False
    try:
        if depth == 0:
            redis_conn = await get_redis_client()._get_redis()
            lock = redis_conn.lock(
                WORKSPACE_UPDATE_LOCK_KEY,
                timeout=WORKSPACE_UPDATE_LOCK_SECONDS,
                blocking_timeout=WORKSPACE_UPDATE_LOCK_WAIT_SECONDS,
            )
            if not await lock.acquire():
                raise RuntimeError(
                    "another workspace Python source update is still in progress"
                )
            lock_acquired = True
            updating_generation = await mark_workspace_generation_updating(
                reason=reason, changed_paths=python_paths
            )
            renewal_task = asyncio.create_task(
                _renew_workspace_update_lease(
                    lock=lock,
                    redis_conn=redis_conn,
                    updating_generation=updating_generation,
                    lease_lost=lease_lost,
                )
            )
        yield
        body_succeeded = True
    finally:
        try:
            lost_before_rotation = await _finish_workspace_source_update(
                depth=depth,
                lock=lock,
                redis_conn=redis_conn,
                updating_generation=updating_generation,
                renewal_task=renewal_task,
                lease_lost=lease_lost,
                reason=reason,
                python_paths=python_paths,
                broadcast=broadcast,
            )
        finally:
            try:
                if lock is not None and lock_acquired:
                    try:
                        await lock.release()
                    except Exception as exc:  # noqa: BLE001 - lock may have expired
                        logger.warning("Workspace update lock release failed: %s", exc)
            finally:
                _workspace_update_depth.reset(token)
        if lost_before_rotation and body_succeeded:
            raise RuntimeError(
                "workspace source update lease was lost before generation rotation"
            )


async def _read_module_from_storage(path: str) -> bytes:
    parts = path.split("/", 2)
    if len(parts) == 3 and parts[0] == SOLUTIONS_ROOT:
        # SolutionStorage reaches file_ops, which imports this cache module.
        # Defer the import until module_cache has finished initializing.
        from src.services.solutions.storage import SolutionStorage

        solution_id, relative_path = parts[1], parts[2]
        return await SolutionStorage(solution_id).read(relative_path)

    if path.startswith(f"{WORKSPACE_RELEASES_ROOT}/"):
        from src.services.workspace_release_storage import (
            WorkspaceReleaseStorage,
            split_workspace_release_storage_path,
        )

        runtime_prefix, relative_path = split_workspace_release_storage_path(path)
        return await WorkspaceReleaseStorage(runtime_prefix).read(relative_path)

    return await RepoStorage().read(path)


async def get_module(path: str) -> CachedModule | None:
    """
    Fetch a module from cache, falling back to S3.

    Lookup order:
    1. Redis cache (fast path)
    2. S3 _repo/ (fallback, re-caches to Redis)
    3. None (module not found)

    Args:
        path: Module path relative to workspace (e.g., "shared/halopsa.py")

    Returns:
        CachedModule dict if found, None otherwise
    """
    redis = get_redis_client()
    key = f"{MODULE_KEY_PREFIX}{path}"
    immutable_module = path.startswith(
        (f"{SOLUTIONS_ROOT}/", f"{WORKSPACE_RELEASES_ROOT}/")
    )

    # A generation is captured before object-storage fallback and rechecked
    # afterwards. A cold read that overlaps a source update can therefore never
    # repopulate Redis with old bytes labelled as the new generation.
    for _attempt in range(3):
        generation = None if immutable_module else await wait_for_workspace_generation()
        cached = _decode_cached_module(await redis.get(key))
        if cached is not None and (
            (
                immutable_module
                and _cached_module_content_matches_hash(cached)
            )
            or _cached_module_is_current(cached, generation or "")
        ):
            return cast(CachedModule, cached)

        try:
            content_bytes = await _read_module_from_storage(path)
        except Exception:
            logger.debug(f"Module not in cache or S3: {log_safe(path)}")
            return None

        if not immutable_module and await wait_for_workspace_generation() != generation:
            continue
        module = _module_from_bytes(
            path=path, content=content_bytes, generation=generation
        )
        if module is None:
            return None
        try:
            await set_module(
                path,
                module["content"],
                module["hash"],
                generation=generation,
            )
        except Exception as exc:
            logger.warning(
                "Failed to re-cache object-storage module: %s", log_safe(exc)
            )
        if immutable_module or await wait_for_workspace_generation() == generation:
            return module

    raise RuntimeError("workspace generation did not stabilize during module read")


async def set_module(
    path: str,
    content: str,
    content_hash: str,
    *,
    generation: str | None = None,
) -> None:
    """
    Cache a module and add to index.

    Called by file_ops when a module is written.

    Args:
        path: Module path relative to workspace
        content: Python source code
        content_hash: SHA-256 hash of content (for change detection)
    """
    redis = get_redis_client()
    key = f"{MODULE_KEY_PREFIX}{path}"

    if not path.startswith(f"{SOLUTIONS_ROOT}/") and generation is None:
        generation = _ready_generation_value(await get_workspace_generation())
    cached = CachedModule(
        content=content,
        path=path,
        hash=content_hash,
        **({"generation": generation} if generation else {}),
    )
    await redis.setex(key, 86400, json.dumps(cached))  # 24hr TTL

    # Add to index set
    redis_conn = await redis._get_redis()
    await cast(Awaitable[int], redis_conn.sadd(MODULE_INDEX_KEY, path))
    if generation:
        await redis_conn.set(MODULE_INDEX_GENERATION_KEY, generation)

    logger.debug(f"Cached module: {log_safe(path)}")


async def invalidate_module(path: str) -> None:
    """
    Remove module from cache and index.

    Called by file_ops when a module is deleted.

    Args:
        path: Module path to invalidate
    """
    redis = get_redis_client()
    key = f"{MODULE_KEY_PREFIX}{path}"

    await redis.delete(key)

    # Remove from index set
    redis_conn = await redis._get_redis()
    await cast(Awaitable[int], redis_conn.srem(MODULE_INDEX_KEY, path))

    logger.debug(f"Invalidated module cache: {log_safe(path)}")


async def get_all_module_paths() -> set[str]:
    """
    Get all cached module paths.

    Used by import hook to check if a module exists in cache.

    Returns:
        Set of all cached module paths
    """
    redis = get_redis_client()
    redis_conn = await redis._get_redis()
    generation = await wait_for_workspace_generation()
    index_generation = _decode_redis_text(
        await redis_conn.get(MODULE_INDEX_GENERATION_KEY)
    )
    paths = await cast(Awaitable[set[str]], redis_conn.smembers(MODULE_INDEX_KEY))
    if index_generation == generation and paths:
        return {p if isinstance(p, str) else p.decode() for p in paths}

    workspace_paths = {
        path for path in await RepoStorage().list() if path.endswith(".py")
    }
    solution_paths = {
        p if isinstance(p, str) else p.decode()
        for p in paths
        if (p if isinstance(p, str) else p.decode()).startswith(f"{SOLUTIONS_ROOT}/")
    }
    rebuilt = workspace_paths | solution_paths
    await redis_conn.delete(MODULE_INDEX_KEY)
    if rebuilt:
        await cast(Awaitable[int], redis_conn.sadd(MODULE_INDEX_KEY, *sorted(rebuilt)))
    await redis_conn.set(MODULE_INDEX_GENERATION_KEY, generation)
    return rebuilt


async def wait_for_workspace_generation(
    *,
    timeout_seconds: float = WORKSPACE_UPDATE_LOCK_WAIT_SECONDS,
    poll_seconds: float = 0.05,
) -> str:
    """Return a ready generation, waiting briefly behind an active writer."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        generation = await get_workspace_generation()
        if not generation.startswith(WORKSPACE_UPDATING_PREFIX):
            return generation
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError("workspace source is still being updated")
        await asyncio.sleep(poll_seconds)


async def inspect_module_coherence(
    durable_hashes: dict[str, str],
) -> tuple[str, list[ModuleCoherence]]:
    """Compare durable Python bytes with Redis content, generation and index."""
    python_hashes = {
        path: value for path, value in durable_hashes.items() if path.endswith(".py")
    }
    if not python_hashes:
        return "", []
    generation = await get_workspace_generation()
    redis = get_redis_client()
    redis_conn = await redis._get_redis()
    ordered = sorted(python_hashes)
    values = await redis_conn.mget([f"{MODULE_KEY_PREFIX}{path}" for path in ordered])
    indexed_raw = await cast(
        Awaitable[set[str | bytes]], redis_conn.smembers(MODULE_INDEX_KEY)
    )
    indexed_paths = {
        value if isinstance(value, str) else value.decode() for value in indexed_raw
    }
    ready_generation = _ready_generation_value(generation)
    evidence: list[ModuleCoherence] = []
    for path, raw in zip(ordered, values, strict=True):
        evidence.append(
            _module_coherence_evidence(
                path=path,
                durable_hash=python_hashes[path],
                raw=raw,
                generation=generation,
                ready_generation=ready_generation,
                indexed=path in indexed_paths,
            )
        )
    return generation, evidence


def _module_coherence_evidence(
    *,
    path: str,
    durable_hash: str,
    raw: object,
    generation: str,
    ready_generation: str,
    indexed: bool,
) -> ModuleCoherence:
    cached = _decode_cached_module(raw) or {}
    cache_hash = cached.get("hash") if isinstance(cached.get("hash"), str) else None
    cache_generation = (
        cached.get("generation") if isinstance(cached.get("generation"), str) else None
    )
    current = _cached_module_is_current(cached, ready_generation)
    updating = generation.startswith(WORKSPACE_UPDATING_PREFIX)
    coherent = (
        not updating
        and indexed
        and (
            not raw
            or (
                cache_hash == durable_hash
                and cache_generation == ready_generation
                and current
            )
        )
    )
    if updating:
        state = "updating"
    elif not raw:
        state = "cold" if indexed else "missing"
    elif cache_hash != durable_hash:
        state = "stale_content"
    elif cache_generation != ready_generation:
        state = "stale_generation"
    elif not indexed:
        state = "missing_index"
    elif not current:
        state = "invalid_content_hash"
    else:
        state = "coherent"
    return ModuleCoherence(
        path=path,
        durable_sha256=durable_hash,
        cache_sha256=cache_hash,
        cache_generation=cache_generation,
        workspace_generation=ready_generation,
        indexed=indexed,
        coherent=coherent,
        state=state,
    )


async def reconcile_module_coherence(
    paths: list[str],
) -> tuple[str, list[ModuleCoherence]]:
    """Populate exact durable bytes into the current ready cache generation."""
    generation = await wait_for_workspace_generation()
    durable_hashes: dict[str, str] = {}
    deleted_paths: list[str] = []
    for path in sorted(set(paths)):
        try:
            content = await _read_module_from_storage(path)
        except Exception:
            await invalidate_module(path)
            deleted_paths.append(path)
            continue
        content_hash = hashlib.sha256(content).hexdigest()
        await set_module(
            path,
            content.decode("utf-8"),
            content_hash,
            generation=generation,
        )
        durable_hashes[path] = content_hash
    if deleted_paths:
        redis = get_redis_client()
        redis_conn = await redis._get_redis()
        deleted_values = await redis_conn.mget(
            [f"{MODULE_KEY_PREFIX}{path}" for path in deleted_paths]
        )
        indexed_raw = await cast(
            Awaitable[set[str | bytes]], redis_conn.smembers(MODULE_INDEX_KEY)
        )
        indexed_paths = {
            value if isinstance(value, str) else value.decode() for value in indexed_raw
        }
        incomplete_deletes = [
            path
            for path, value in zip(deleted_paths, deleted_values, strict=True)
            if value is not None or path in indexed_paths
        ]
        if incomplete_deletes:
            raise WorkspacePropagationError(
                "workspace runtime deletion did not converge for: "
                + ", ".join(incomplete_deletes)
            )
    if not durable_hashes:
        return generation, []
    observed_generation, evidence = await inspect_module_coherence(durable_hashes)
    mismatches = [item.path for item in evidence if not item.coherent]
    if observed_generation != generation or mismatches:
        raise WorkspacePropagationError(
            "workspace runtime propagation did not converge for: "
            + ", ".join(mismatches or paths)
        )
    return generation, evidence


async def refresh_modules_from_directory(work_dir: Path) -> int:
    """Re-populate Redis module cache from local .py files after sync.

    Called after S3 sync to ensure Redis cache matches the just-synced
    content, preventing stale reads by the editor and workflow engine.

    Args:
        work_dir: Local directory containing the synced files.

    Returns:
        Number of modules refreshed.
    """
    python_files = list(work_dir.rglob("*.py"))
    python_paths = [str(path.relative_to(work_dir)) for path in python_files]
    count = 0
    async with workspace_source_update(
        reason="workspace_directory_refresh",
        changed_paths=python_paths,
        broadcast=True,
    ):
        for py_file, rel_path in zip(python_files, python_paths, strict=True):
            content_bytes = py_file.read_bytes()
            try:
                content_str = content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                continue
            content_hash = hashlib.sha256(content_bytes).hexdigest()
            await set_module(rel_path, content_str, content_hash)
            count += 1
    logger.info(f"Refreshed {count} module(s) in Redis cache from {log_safe(work_dir)}")
    return count


async def clear_module_cache() -> int:
    """
    Clear all cached modules.

    Used for testing and cache invalidation.

    Returns:
        Number of modules cleared
    """
    redis = get_redis_client()
    redis_conn = await redis._get_redis()

    # Get all module paths from index
    paths = await cast(Awaitable[set[str]], redis_conn.smembers(MODULE_INDEX_KEY))
    count = len(paths)

    if paths:
        # Delete all module keys
        keys = [
            f"{MODULE_KEY_PREFIX}{p if isinstance(p, str) else p.decode()}"
            for p in paths
        ]
        await cast(Awaitable[int], redis_conn.delete(*keys))

    # Clear the index
    await cast(Awaitable[int], redis_conn.delete(MODULE_INDEX_KEY))

    logger.info(f"Cleared {count} modules from cache")
    return count
