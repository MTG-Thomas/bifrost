import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_publish_file_activity_broadcasts_to_channel():
    with patch("src.core.pubsub.manager") as mock_manager:
        mock_manager.broadcast = AsyncMock()
        from src.core.pubsub import publish_file_activity
        await publish_file_activity(
            user_id="user-1",
            user_name="Jack",
            activity_type="file_push",
            prefix="apps/portal",
            file_count=4,
            is_watch=False,
        )
        mock_manager.broadcast.assert_called_once()
        channel, message = mock_manager.broadcast.call_args[0]
        assert channel == "file-activity"
        assert message["type"] == "file_push"
        assert message["user_name"] == "Jack"
        assert message["prefix"] == "apps/portal"
        assert message["file_count"] == 4
        assert message["is_watch"] is False
        assert "timestamp" in message


@pytest.mark.asyncio
async def test_publish_file_activity_watch_start():
    with patch("src.core.pubsub.manager") as mock_manager:
        mock_manager.broadcast = AsyncMock()
        from src.core.pubsub import publish_file_activity
        await publish_file_activity(
            user_id="user-1",
            user_name="Jack",
            activity_type="watch_start",
            prefix="apps/portal",
        )
        channel, message = mock_manager.broadcast.call_args[0]
        assert channel == "file-activity"
        assert message["type"] == "watch_start"
