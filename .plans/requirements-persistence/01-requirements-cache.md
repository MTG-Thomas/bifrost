# Phase 1: Requirements Cache Module

Create `api/src/core/requirements_cache.py` following the pattern of `module_cache.py`.

## Overview

This module provides Redis caching for requirements.txt content:
- Store requirements.txt content in Redis with 24-hour TTL
- Warm cache from database at startup
- Save requirements to both database and cache after package install

## File: `api/src/core/requirements_cache.py`

## Reference: `api/src/core/module_cache.py`

Follow the existing patterns from module_cache.py for:
- Redis client usage via `get_redis_client()`
- JSON serialization of cached data
- 24-hour TTL (86400 seconds)
- Database session handling (optional session parameter)

## Tasks

### Task 1.1: Create file with imports and constants
- [ ] Create `api/src/core/requirements_cache.py`
- [ ] Add imports: `json`, `logging`, `hashlib`, `TypedDict`
- [ ] Import `get_redis_client` from `src.core.redis_client`
- [ ] Define constant: `REQUIREMENTS_KEY = "bifrost:requirements:content"`
- [ ] Define `CachedRequirements(TypedDict)` with fields: `content: str`, `hash: str`
- [ ] Add module docstring explaining purpose

```python
"""
Async Redis client for requirements.txt caching.

Used by API services and background jobs that have async context.
Workers read requirements from this cache during startup to install packages.

Key pattern:
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
```

### Task 1.2: Implement get_requirements()
- [ ] Implement `async get_requirements() -> CachedRequirements | None`
- [ ] Fetch from Redis key `REQUIREMENTS_KEY`
- [ ] Parse JSON and return `CachedRequirements` or `None` if not found

```python
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
```

### Task 1.3: Implement set_requirements()
- [ ] Implement `async set_requirements(content: str, content_hash: str) -> None`
- [ ] Store JSON with content and hash
- [ ] Set 24-hour TTL (86400 seconds)
- [ ] Add debug logging

```python
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
```

### Task 1.4: Implement warm_requirements_cache()
- [ ] Implement `async warm_requirements_cache(session=None) -> bool`
- [ ] Query `WorkspaceFile` where `path='requirements.txt'` AND `entity_type='requirements'` AND `is_deleted=False`
- [ ] If found with content, call `set_requirements()`
- [ ] Return `True` if cached, `False` if not found
- [ ] Handle optional session parameter like module_cache.py does

```python
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
```

### Task 1.5: Implement save_requirements_to_db()
- [ ] Implement `async save_requirements_to_db(content: str, session=None) -> None`
- [ ] Compute SHA-256 hash of content
- [ ] Upsert `WorkspaceFile` record with:
  - `path="requirements.txt"`
  - `entity_type="requirements"`
  - `content=content`
  - `content_hash=hash`
  - `size_bytes=len(content)`
  - `content_type="text/plain"`
- [ ] Update Redis cache via `set_requirements()`
- [ ] Handle optional session parameter

```python
async def save_requirements_to_db(content: str, session=None) -> None:
    """
    Save requirements.txt to database and update cache.

    Args:
        content: Full requirements.txt content
        session: Optional SQLAlchemy AsyncSession. If not provided, creates its own.
    """
    from sqlalchemy import select
    from src.models.orm.workspace import WorkspaceFile
    from src.models.enums import GitStatus

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
```

### Task 1.6: Add unit tests
- [ ] Create `api/tests/unit/core/test_requirements_cache.py`
- [ ] Test `get_requirements()` with mocked Redis returning data
- [ ] Test `get_requirements()` with mocked Redis returning None
- [ ] Test `set_requirements()` calls Redis with correct key and TTL
- [ ] Test `warm_requirements_cache()` with mocked database and Redis
- [ ] Test `save_requirements_to_db()` with mocked database and Redis

```python
"""Unit tests for requirements_cache module."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.requirements_cache import (
    REQUIREMENTS_KEY,
    get_requirements,
    set_requirements,
    warm_requirements_cache,
    save_requirements_to_db,
)


class TestGetRequirements:
    @pytest.mark.asyncio
    async def test_returns_cached_data(self):
        """Test get_requirements returns cached data."""
        cached = {"content": "flask==2.3.0\n", "hash": "abc123"}

        with patch("src.core.requirements_cache.get_redis_client") as mock_redis:
            mock_client = MagicMock()
            mock_client.get = AsyncMock(return_value=json.dumps(cached))
            mock_redis.return_value = mock_client

            result = await get_requirements()

            assert result == cached
            mock_client.get.assert_called_once_with(REQUIREMENTS_KEY)

    @pytest.mark.asyncio
    async def test_returns_none_when_not_cached(self):
        """Test get_requirements returns None when not in cache."""
        with patch("src.core.requirements_cache.get_redis_client") as mock_redis:
            mock_client = MagicMock()
            mock_client.get = AsyncMock(return_value=None)
            mock_redis.return_value = mock_client

            result = await get_requirements()

            assert result is None


class TestSetRequirements:
    @pytest.mark.asyncio
    async def test_caches_with_ttl(self):
        """Test set_requirements stores with correct TTL."""
        content = "flask==2.3.0\n"
        content_hash = "abc123"

        with patch("src.core.requirements_cache.get_redis_client") as mock_redis:
            mock_client = MagicMock()
            mock_client.setex = AsyncMock()
            mock_redis.return_value = mock_client

            await set_requirements(content, content_hash)

            mock_client.setex.assert_called_once()
            call_args = mock_client.setex.call_args
            assert call_args[0][0] == REQUIREMENTS_KEY
            assert call_args[0][1] == 86400  # 24 hours

            cached = json.loads(call_args[0][2])
            assert cached["content"] == content
            assert cached["hash"] == content_hash
```

## Verification

```bash
./test.sh tests/unit/core/test_requirements_cache.py -v
```

## Checklist

- [ ] File created: `api/src/core/requirements_cache.py`
- [ ] `get_requirements()` implemented
- [ ] `set_requirements()` implemented
- [ ] `warm_requirements_cache()` implemented
- [ ] `save_requirements_to_db()` implemented
- [ ] Unit tests created and passing
- [ ] No type errors: `cd api && pyright src/core/requirements_cache.py`
- [ ] No lint errors: `cd api && ruff check src/core/requirements_cache.py`
