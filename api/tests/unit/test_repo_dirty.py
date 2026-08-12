import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest


@asynccontextmanager
async def _redis_context(redis):
    yield redis


def _mock_redis_context(mock_redis):
    return patch(
        "src.core.repo_dirty.get_redis",
        side_effect=lambda: _redis_context(mock_redis),
    )


@pytest.mark.asyncio
async def test_mark_repo_dirty_sets_generation_record():
    mock_redis = AsyncMock()
    mock_redis.eval.side_effect = lambda _script, _count, _key, value: value
    with _mock_redis_context(mock_redis):
        from src.core.repo_dirty import mark_repo_dirty
        state = await mark_repo_dirty(writer="changeset:123")

    assert state.generation
    assert state.writer == "changeset:123"
    assert state.dirty_since == state.updated_at
    assert mock_redis.eval.call_args.args[2] == "bifrost:repo_dirty"
    stored = json.loads(mock_redis.eval.call_args.args[3])
    assert stored["generation"] == state.generation


@pytest.mark.asyncio
async def test_clear_repo_dirty_deletes_redis_key():
    mock_redis = AsyncMock()
    with _mock_redis_context(mock_redis):
        from src.core.repo_dirty import clear_repo_dirty
        await clear_repo_dirty()
        mock_redis.delete.assert_called_once_with("bifrost:repo_dirty")


@pytest.mark.asyncio
async def test_timestamp_only_dirty_marker_remains_visible_but_legacy():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = "2026-02-19T12:00:00+00:00"
    with _mock_redis_context(mock_redis):
        from src.core.repo_dirty import get_repo_dirty_since, get_repo_dirty_state
        state = await get_repo_dirty_state()
        result = await get_repo_dirty_since()
        assert state is not None
        assert state.legacy is True
        assert state.generation is None
        assert result == "2026-02-19T12:00:00+00:00"


@pytest.mark.asyncio
async def test_is_repo_dirty_returns_none_when_clean():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    with _mock_redis_context(mock_redis):
        from src.core.repo_dirty import get_repo_dirty_since
        result = await get_repo_dirty_since()
        assert result is None


@pytest.mark.asyncio
async def test_reconcile_repo_dirty_uses_atomic_generation_compare():
    mock_redis = AsyncMock()
    mock_redis.eval.return_value = 1
    with _mock_redis_context(mock_redis):
        from src.core.repo_dirty import reconcile_repo_dirty

        assert await reconcile_repo_dirty("generation-a") is True

    assert mock_redis.eval.call_args.args[2:] == (
        "bifrost:repo_dirty",
        "generation-a",
    )


@pytest.mark.asyncio
async def test_reconcile_repo_dirty_preserves_later_generation():
    mock_redis = AsyncMock()
    mock_redis.eval.return_value = 0
    with _mock_redis_context(mock_redis):
        from src.core.repo_dirty import reconcile_repo_dirty

        assert await reconcile_repo_dirty("older-generation") is False
