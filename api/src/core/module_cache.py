"""
Async Redis client for module caching.

Used by API services and background jobs that have async context.
Workers read modules from this cache during virtual imports.

Key patterns:
- bifrost:module:{path} - JSON: {content, path, hash}
- bifrost:module:index - SET of all module paths
"""

import hashlib
import json
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import AsyncIterator, Awaitable, TypedDict, cast
from uuid import uuid4

from src.core.log_safety import log_safe
from src.core.redis_client import get_redis_client
from src.services.repo_storage import RepoStorage

logger = logging.getLogger(__name__)

MODULE_KEY_PREFIX = "bifrost:module:"
MODULE_INDEX_KEY = "bifrost:module:index"
WORKSPACE_GENERATION_KEY = "bifrost:workspace:generation"
WORKSPACE_GENERATION_CHANNEL = "bifrost:workspace:generation:events"
WORKSPACE_UPDATING_PREFIX = "updating:"
WORKSPACE_UPDATE_LOCK_KEY = "bifrost:workspace:generation:update-lock"
WORKSPACE_UPDATE_LOCK_SECONDS = 300
SOLUTIONS_ROOT = "_solutions"
_workspace_update_depth: ContextVar[int] = ContextVar(
    "workspace_update_depth", default=0
)


class CachedModule(TypedDict):
    """Schema for cached module data."""

    content: str
    path: str
    hash: str


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
) -> str:
    """Replace the workspace generation after Python source becomes active.

    Redis is the worker module-cache authority. Updating the generation after
    source/cache writes gives every execution a cheap coherence check. Release
    activation also broadcasts the new generation so worker templates recycle
    promptly; the per-execution check remains authoritative if pub/sub is lost.
    """
    redis = get_redis_client()
    redis_conn = await redis._get_redis()
    generation = uuid4().hex
    await redis_conn.set(WORKSPACE_GENERATION_KEY, generation)

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
    await redis_conn.set(WORKSPACE_GENERATION_KEY, generation)
    logger.info(
        "Workspace generation marked updating (%s, %s Python path(s))",
        reason,
        len(set(changed_paths)),
    )
    return generation


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
    marked_updating = False
    try:
        if depth == 0:
            redis_conn = await get_redis_client()._get_redis()
            lock = redis_conn.lock(
                WORKSPACE_UPDATE_LOCK_KEY,
                timeout=WORKSPACE_UPDATE_LOCK_SECONDS,
                blocking_timeout=WORKSPACE_UPDATE_LOCK_SECONDS,
            )
            if not await lock.acquire():
                raise RuntimeError(
                    "another workspace Python source update is still in progress"
                )
            await mark_workspace_generation_updating(
                reason=reason, changed_paths=python_paths
            )
            marked_updating = True
        yield
    finally:
        try:
            if depth == 0 and marked_updating:
                await rotate_workspace_generation(
                    reason=reason,
                    changed_paths=python_paths,
                    broadcast=broadcast,
                )
        finally:
            try:
                if lock is not None:
                    try:
                        await lock.release()
                    except Exception as exc:  # noqa: BLE001 - lock may have expired
                        logger.warning("Workspace update lock release failed: %s", exc)
            finally:
                _workspace_update_depth.reset(token)


async def _read_module_from_storage(path: str) -> bytes:
    parts = path.split("/", 2)
    if len(parts) == 3 and parts[0] == SOLUTIONS_ROOT:
        # SolutionStorage reaches file_ops, which imports this cache module.
        # Defer the import until module_cache has finished initializing.
        from src.services.solutions.storage import SolutionStorage

        solution_id, relative_path = parts[1], parts[2]
        return await SolutionStorage(solution_id).read(relative_path)

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
    data = await redis.get(key)
    if data:
        return json.loads(data)

    # Redis miss — try S3 fallback
    try:
        content_bytes = await _read_module_from_storage(path)
    except Exception:
        logger.debug(f"Module not in cache or S3: {log_safe(path)}")
        return None

    try:
        content_str = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning(f"Could not decode {log_safe(path)} as UTF-8, skipping")
        return None

    content_hash = hashlib.sha256(content_bytes).hexdigest()
    module: CachedModule = {
        "content": content_str,
        "path": path,
        "hash": content_hash,
    }

    # Re-cache to Redis (self-healing)
    try:
        await redis.setex(key, 86400, json.dumps(module))
        redis_conn = await redis._get_redis()
        await cast(Awaitable[int], redis_conn.sadd(MODULE_INDEX_KEY, path))
    except Exception as e:
        logger.warning(f"Failed to re-cache S3 module to Redis: {log_safe(e)}")

    return module


async def set_module(path: str, content: str, content_hash: str) -> None:
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

    cached = CachedModule(content=content, path=path, hash=content_hash)
    await redis.setex(key, 86400, json.dumps(cached))  # 24hr TTL

    # Add to index set
    redis_conn = await redis._get_redis()
    await cast(Awaitable[int], redis_conn.sadd(MODULE_INDEX_KEY, path))

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
    paths = await cast(Awaitable[set[str]], redis_conn.smembers(MODULE_INDEX_KEY))
    return {p if isinstance(p, str) else p.decode() for p in paths}


async def refresh_modules_from_directory(work_dir: Path) -> int:
    """Re-populate Redis module cache from local .py files after sync.

    Called after S3 sync to ensure Redis cache matches the just-synced
    content, preventing stale reads by the editor and workflow engine.

    Args:
        work_dir: Local directory containing the synced files.

    Returns:
        Number of modules refreshed.
    """
    count = 0
    for py_file in work_dir.rglob("*.py"):
        rel_path = str(py_file.relative_to(work_dir))
        content_bytes = py_file.read_bytes()
        try:
            content_str = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            continue
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        await set_module(rel_path, content_str, content_hash)
        count += 1
    logger.info(
        f"Refreshed {count} module(s) in Redis cache from {log_safe(work_dir)}"
    )
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
        keys = [f"{MODULE_KEY_PREFIX}{p if isinstance(p, str) else p.decode()}" for p in paths]
        await cast(Awaitable[int], redis_conn.delete(*keys))

    # Clear the index
    await cast(Awaitable[int], redis_conn.delete(MODULE_INDEX_KEY))

    logger.info(f"Cleared {count} modules from cache")
    return count
