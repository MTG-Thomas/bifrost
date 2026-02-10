"""Tests for sync lock."""
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_lock_acquired():
    """Should acquire lock when not held."""
    from src.services.sync_lock import acquire_sync_lock

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)

    result = await acquire_sync_lock(mock_redis)
    assert result is True
    mock_redis.set.assert_called_once_with("bifrost:sync:lock", "1", nx=True, ex=300)


@pytest.mark.asyncio
async def test_lock_rejected_when_held():
    """Should reject lock when already held."""
    from src.services.sync_lock import acquire_sync_lock

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=False)

    result = await acquire_sync_lock(mock_redis)
    assert result is False


@pytest.mark.asyncio
async def test_lock_custom_ttl():
    """Should use custom TTL."""
    from src.services.sync_lock import acquire_sync_lock

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)

    result = await acquire_sync_lock(mock_redis, ttl=60)
    assert result is True
    mock_redis.set.assert_called_once_with("bifrost:sync:lock", "1", nx=True, ex=60)


@pytest.mark.asyncio
async def test_release_lock():
    """Should release the lock."""
    from src.services.sync_lock import release_sync_lock

    mock_redis = AsyncMock()
    mock_redis.delete = AsyncMock()

    await release_sync_lock(mock_redis)
    mock_redis.delete.assert_called_once_with("bifrost:sync:lock")


@pytest.mark.asyncio
async def test_lock_acquire_release_cycle():
    """Full acquire-release cycle should work."""
    from src.services.sync_lock import acquire_sync_lock, release_sync_lock

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock()

    # Acquire
    assert await acquire_sync_lock(mock_redis) is True
    # Release
    await release_sync_lock(mock_redis)
    # Acquire again
    assert await acquire_sync_lock(mock_redis) is True
