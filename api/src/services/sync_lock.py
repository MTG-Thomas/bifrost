"""
Redis distributed lock for git sync operations.

Prevents concurrent git sync operations from corrupting state.
Uses Redis SET NX EX pattern for atomic lock acquisition.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SYNC_LOCK_KEY = "bifrost:sync:lock"
SYNC_LOCK_TTL = 300  # 5 minutes


async def acquire_sync_lock(redis_client, ttl: int = SYNC_LOCK_TTL) -> bool:
    """
    Acquire the git sync lock.

    Uses Redis SET NX EX for atomic lock acquisition.
    Returns True if lock acquired, False if already held.
    """
    result = await redis_client.set(SYNC_LOCK_KEY, "1", nx=True, ex=ttl)
    if result:
        logger.info(f"Acquired sync lock (TTL={ttl}s)")
    else:
        logger.info("Sync lock already held, skipping")
    return bool(result)


async def release_sync_lock(redis_client) -> None:
    """Release the git sync lock."""
    await redis_client.delete(SYNC_LOCK_KEY)
    logger.info("Released sync lock")
