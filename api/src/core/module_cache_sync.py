"""
Synchronous Redis client for import hook.

Python's import system runs synchronously - we need sync Redis access
for the MetaPathFinder to fetch modules during import.

This module provides synchronous versions of the cache functions
specifically for use in virtual_import.py's MetaPathFinder.

When a cache miss occurs, we fall back to the database to fetch the module
and re-cache it. This provides self-healing behavior when:
- Redis cache entries expire (24hr TTL)
- Redis restarts or evicts keys
- Cache warming at startup was incomplete
"""

import json
import logging
import os
from functools import lru_cache
from typing import Any

import redis

from src.core.module_cache import MODULE_INDEX_KEY, MODULE_KEY_PREFIX, CachedModule

logger = logging.getLogger(__name__)

# TTL for cached modules (24 hours)
MODULE_CACHE_TTL = 86400


@lru_cache(maxsize=1)
def _get_sync_redis() -> Any:
    """
    Get synchronous Redis client.

    Uses lru_cache to reuse connection across imports.

    Note: Returns Any because redis-py's type stubs are a union of sync/async
    which confuses type checkers. This is a sync-only module.
    """
    return redis.Redis.from_url(
        os.environ.get("BIFROST_REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


def get_module_sync(path: str) -> CachedModule | None:
    """
    Fetch a single module from cache (synchronous).

    Called by VirtualModuleFinder.find_spec() during import resolution.

    Falls back to database if not in cache, providing self-healing
    behavior when cache entries expire or are evicted.

    Args:
        path: Module path relative to workspace

    Returns:
        CachedModule dict if found, None otherwise
    """
    try:
        client = _get_sync_redis()
        key = f"{MODULE_KEY_PREFIX}{path}"
        data = client.get(key)
        if data:
            return json.loads(data)

        # Cache miss - try database fallback
        return _fetch_and_cache_from_db_sync(path, client)

    except redis.RedisError as e:
        logger.warning(f"Redis error fetching module {path}: {e}")
        return None


def _fetch_and_cache_from_db_sync(path: str, client: Any) -> CachedModule | None:
    """
    Fetch module from database and re-cache it.

    Uses SQLAlchemy sync engine since the import hook is synchronous.
    This provides self-healing cache behavior when entries expire.

    Args:
        path: Module path relative to workspace
        client: Sync Redis client instance

    Returns:
        CachedModule dict if found in DB, None otherwise
    """
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from src.config import get_settings
    from src.models import WorkspaceFile

    try:
        settings = get_settings()
        engine = create_engine(settings.database_url_sync)

        with Session(engine) as session:
            stmt = select(WorkspaceFile).where(
                WorkspaceFile.path == path,
                WorkspaceFile.entity_type == "module",
                WorkspaceFile.is_deleted == False,  # noqa: E712
                WorkspaceFile.content.isnot(None),
            )
            file = session.execute(stmt).scalar_one_or_none()

        if not file:
            return None

        cached: CachedModule = {
            "content": file.content or "",
            "path": path,
            "hash": file.content_hash or "",
        }

        # Re-cache with TTL
        key = f"{MODULE_KEY_PREFIX}{path}"
        client.setex(key, MODULE_CACHE_TTL, json.dumps(cached))

        # Update the index
        client.sadd(MODULE_INDEX_KEY, path)

        logger.info(f"Re-cached module from DB: {path}")
        return cached

    except Exception as e:
        logger.warning(f"DB fallback failed for {path}: {e}")
        return None


def get_module_index_sync() -> set[str]:
    """
    Get all cached module paths (synchronous).

    Called by VirtualModuleFinder to build the module index.

    Returns:
        Set of all cached module paths
    """
    try:
        client = _get_sync_redis()
        paths = client.smembers(MODULE_INDEX_KEY)
        return {p if isinstance(p, str) else p.decode() for p in paths}
    except redis.RedisError as e:
        logger.warning(f"Redis error fetching module index: {e}")
        return set()


def reset_sync_redis() -> None:
    """
    Reset the sync Redis client.

    Used for testing to clear the cached connection.
    """
    _get_sync_redis.cache_clear()
