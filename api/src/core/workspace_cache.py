"""
Workspace File Cache for Fast Lookups

Redis cache for workspace file state, used by the watcher to quickly determine
if a local change needs to be synced (or if it came from pub/sub).

Key format: workspace:file:{path}
Value: JSON with {hash, is_deleted}

Dual-write pattern: Whenever DB is updated, Redis cache is updated atomically
with the same values. This ensures cache consistency without TTL expiration.
"""

import json
import logging
from typing import TypedDict

import redis.asyncio as redis

from src.config import get_settings

logger = logging.getLogger(__name__)

# Redis key prefix for workspace file cache
WORKSPACE_FILE_PREFIX = "workspace:file:"


class WorkspaceFileCache(TypedDict):
    """Schema for cached workspace file state."""
    hash: str | None  # Content hash (None for folders/deleted)
    is_deleted: bool


class WorkspaceCacheClient:
    """
    Redis cache client for workspace file state.

    Provides fast lookups for the workspace watcher to determine if
    a local change originated locally or came from pub/sub.
    """

    def __init__(self):
        self._redis: redis.Redis | None = None

    async def _get_redis(self) -> redis.Redis:
        """Get or create Redis connection."""
        if self._redis is None:
            settings = get_settings()
            self._redis = redis.from_url(
                settings.redis_url,
                decode_responses=True,
            )
        return self._redis

    async def set_file_state(
        self,
        path: str,
        content_hash: str | None,
        is_deleted: bool = False,
    ) -> None:
        """
        Set cached state for a workspace file.

        Called by FileStorageService after every DB write/delete.

        Args:
            path: Workspace file path
            content_hash: SHA-256 hash of content (None for folders/deleted)
            is_deleted: Whether the file is soft-deleted
        """
        redis_client = await self._get_redis()
        key = f"{WORKSPACE_FILE_PREFIX}{path}"

        data: WorkspaceFileCache = {
            "hash": content_hash,
            "is_deleted": is_deleted,
        }

        try:
            # No TTL - cache must be accurate at all times
            # Dual-write pattern ensures consistency
            await redis_client.set(key, json.dumps(data))
            logger.debug(f"Set workspace cache: {path} (hash={content_hash[:8] if content_hash else None}, deleted={is_deleted})")
        except Exception as e:
            logger.warning(f"Failed to set workspace cache for {path}: {e}")
            # Don't raise - cache failure shouldn't fail the write

    async def get_file_state(self, path: str) -> WorkspaceFileCache | None:
        """
        Get cached state for a workspace file.

        Called by workspace watcher to check if local change needs syncing.

        Args:
            path: Workspace file path

        Returns:
            WorkspaceFileCache dict or None if not in cache
        """
        redis_client = await self._get_redis()
        key = f"{WORKSPACE_FILE_PREFIX}{path}"

        try:
            data = await redis_client.get(key)
            if data is None:
                return None
            return json.loads(data)
        except Exception as e:
            logger.warning(f"Failed to get workspace cache for {path}: {e}")
            return None

    async def delete_file_state(self, path: str) -> None:
        """
        Delete cached state for a workspace file.

        Called when a file is permanently removed (not soft-deleted).

        Args:
            path: Workspace file path
        """
        redis_client = await self._get_redis()
        key = f"{WORKSPACE_FILE_PREFIX}{path}"

        try:
            await redis_client.delete(key)
            logger.debug(f"Deleted workspace cache: {path}")
        except Exception as e:
            logger.warning(f"Failed to delete workspace cache for {path}: {e}")

    async def delete_folder_states(self, folder_path: str) -> None:
        """
        Delete cached states for all files under a folder.

        Args:
            folder_path: Folder path (with or without trailing slash)
        """
        redis_client = await self._get_redis()
        prefix = f"{WORKSPACE_FILE_PREFIX}{folder_path.rstrip('/')}/"

        try:
            # Use SCAN to find matching keys (safer than KEYS for production)
            cursor = 0
            while True:
                cursor, keys = await redis_client.scan(
                    cursor=cursor,
                    match=f"{prefix}*",
                    count=100,
                )
                if keys:
                    await redis_client.delete(*keys)
                if cursor == 0:
                    break
            logger.debug(f"Deleted workspace cache for folder: {folder_path}")
        except Exception as e:
            logger.warning(f"Failed to delete workspace cache for folder {folder_path}: {e}")

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None


# Singleton instance
_workspace_cache: WorkspaceCacheClient | None = None


def get_workspace_cache() -> WorkspaceCacheClient:
    """Get singleton workspace cache client instance."""
    global _workspace_cache
    if _workspace_cache is None:
        _workspace_cache = WorkspaceCacheClient()
    return _workspace_cache


async def close_workspace_cache() -> None:
    """Close workspace cache client."""
    global _workspace_cache
    if _workspace_cache:
        await _workspace_cache.close()
        _workspace_cache = None
