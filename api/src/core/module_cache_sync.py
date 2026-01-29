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

    If module is not in Redis cache, returns None. The consumer is responsible
    for ensuring the cache is warm before dispatching to workers.

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

        # No DB fallback - consumer ensures cache is warm
        logger.debug(f"Module not in cache: {path}")
        return None

    except redis.RedisError as e:
        logger.warning(f"Redis error fetching module {path}: {e}")
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
