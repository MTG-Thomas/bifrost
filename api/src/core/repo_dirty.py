"""
Repo dirty flag — tracks when platform-side writes have occurred
since the last git sync. Used by CLI push to fast-check staleness.

Set by: FileStorageService.write_file() (when NOT from CLI push)
Cleared by: GitHubSyncService on successful sync
Checked by: GET /api/github/repo-status
"""
from datetime import datetime, timezone

from src.core.cache.redis_client import get_shared_redis

DIRTY_KEY = "bifrost:repo_dirty"


async def mark_repo_dirty() -> None:
    """Mark repo as having uncommitted platform changes."""
    r = await get_shared_redis()
    await r.set(DIRTY_KEY, datetime.now(timezone.utc).isoformat())


async def clear_repo_dirty() -> None:
    """Clear dirty flag after successful git sync."""
    r = await get_shared_redis()
    await r.delete(DIRTY_KEY)


async def get_repo_dirty_since() -> str | None:
    """Return ISO timestamp if dirty, None if clean."""
    r = await get_shared_redis()
    val = await r.get(DIRTY_KEY)
    return val if val else None
