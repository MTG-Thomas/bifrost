import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_mark_repo_dirty_sets_redis_key():
    mock_redis = AsyncMock()
    with patch("src.core.repo_dirty.get_shared_redis", return_value=mock_redis):
        from src.core.repo_dirty import mark_repo_dirty
        await mark_repo_dirty()
        mock_redis.set.assert_called_once()
        key = mock_redis.set.call_args[0][0]
        assert key == "bifrost:repo_dirty"


@pytest.mark.asyncio
async def test_clear_repo_dirty_deletes_redis_key():
    mock_redis = AsyncMock()
    with patch("src.core.repo_dirty.get_shared_redis", return_value=mock_redis):
        from src.core.repo_dirty import clear_repo_dirty
        await clear_repo_dirty()
        mock_redis.delete.assert_called_once_with("bifrost:repo_dirty")


@pytest.mark.asyncio
async def test_is_repo_dirty_returns_timestamp_when_set():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = "2026-02-19T12:00:00+00:00"
    with patch("src.core.repo_dirty.get_shared_redis", return_value=mock_redis):
        from src.core.repo_dirty import get_repo_dirty_since
        result = await get_repo_dirty_since()
        assert result == "2026-02-19T12:00:00+00:00"


@pytest.mark.asyncio
async def test_is_repo_dirty_returns_none_when_clean():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    with patch("src.core.repo_dirty.get_shared_redis", return_value=mock_redis):
        from src.core.repo_dirty import get_repo_dirty_since
        result = await get_repo_dirty_since()
        assert result is None
