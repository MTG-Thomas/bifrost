"""
Requirements.txt Persistence Architecture
=========================================

This module is part of the package persistence system that ensures
installed packages survive container restarts.

Persistence Flow:
1. User installs package via /api/packages/install
2. package_install.py consumer installs the package and calls save_requirements_to_db()
3. requirements.txt is stored in workspace_files table + Redis cache
4. On container restart, init_container.py calls warm_requirements_cache()
5. Worker processes call _install_requirements_from_cache_sync() at startup
6. pip install runs from cached requirements.txt

Related Files:
- api/src/jobs/consumers/package_install.py - Saves after install
- api/scripts/init_container.py - Warms cache on container startup
- api/src/services/execution/simple_worker.py - Installs on worker startup

Key Pattern:
- bifrost:requirements:content - JSON: {content, hash}
"""

import hashlib
import json
import logging
from typing import TypedDict

from src.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

REQUIREMENTS_KEY = "bifrost:requirements:content"


class CachedRequirements(TypedDict):
    """Schema for cached requirements data."""

    content: str
    hash: str


async def get_requirements() -> CachedRequirements | None:
    """
    Fetch requirements.txt content from cache.

    Returns:
        CachedRequirements dict if found, None otherwise
    """
    redis = get_redis_client()
    data = await redis.get(REQUIREMENTS_KEY)
    if data:
        return json.loads(data)
    return None


async def set_requirements(content: str, content_hash: str) -> None:
    """
    Cache requirements.txt content.

    Args:
        content: Full requirements.txt content
        content_hash: SHA-256 hash of content (for change detection)
    """
    redis = get_redis_client()
    cached = CachedRequirements(content=content, hash=content_hash)
    await redis.setex(REQUIREMENTS_KEY, 86400, json.dumps(cached))  # 24hr TTL
    logger.debug("Cached requirements.txt")


async def warm_requirements_cache(session=None) -> bool:
    """
    Load requirements.txt from database into Redis cache.

    Called by init container or API startup to ensure cache is warm.

    Args:
        session: Optional SQLAlchemy AsyncSession. If not provided, creates its own.

    Returns:
        True if requirements.txt was cached, False if not found
    """
    from sqlalchemy import select

    from src.models.orm.workspace import WorkspaceFile

    async def _warm_with_session(db_session) -> bool:
        stmt = select(WorkspaceFile).where(
            WorkspaceFile.path == "requirements.txt",
            WorkspaceFile.entity_type == "requirements",
            WorkspaceFile.is_deleted == False,  # noqa: E712
            WorkspaceFile.content.isnot(None),
        )
        result = await db_session.execute(stmt)
        file = result.scalar_one_or_none()

        if file and file.content:
            await set_requirements(
                content=file.content,
                content_hash=file.content_hash or "",
            )
            logger.info("Warmed requirements cache from database")
            return True

        logger.info("No requirements.txt found in database")
        return False

    if session is not None:
        return await _warm_with_session(session)
    else:
        from src.core.database import get_db_context

        async with get_db_context() as db_session:
            return await _warm_with_session(db_session)


async def save_requirements_to_db(content: str, session=None) -> None:
    """
    Save requirements.txt to database and update cache.

    Args:
        content: Full requirements.txt content
        session: Optional SQLAlchemy AsyncSession. If not provided, creates its own.
    """
    from sqlalchemy import select

    from src.models.enums import GitStatus
    from src.models.orm.workspace import WorkspaceFile

    content_hash = hashlib.sha256(content.encode()).hexdigest()

    async def _save_with_session(db_session) -> None:
        # Check if record exists
        stmt = select(WorkspaceFile).where(
            WorkspaceFile.path == "requirements.txt",
            WorkspaceFile.entity_type == "requirements",
        )
        result = await db_session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing record
            existing.content = content
            existing.content_hash = content_hash
            existing.size_bytes = len(content)
            existing.is_deleted = False
        else:
            # Create new record
            file = WorkspaceFile(
                path="requirements.txt",
                entity_type="requirements",
                content=content,
                content_hash=content_hash,
                size_bytes=len(content),
                content_type="text/plain",
                git_status=GitStatus.UNTRACKED,
                is_deleted=False,
            )
            db_session.add(file)

        await db_session.commit()

        # Update cache
        await set_requirements(content, content_hash)
        logger.info("Saved requirements.txt to database and cache")

    if session is not None:
        await _save_with_session(session)
    else:
        from src.core.database import get_db_context

        async with get_db_context() as db_session:
            await _save_with_session(db_session)
