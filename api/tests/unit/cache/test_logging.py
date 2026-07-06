"""
Unit tests for the unified log streaming module.

Tests the Redis Stream-based logging that replaces the old sync Postgres approach.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from bifrost import _logging
from bifrost._logging import (
    append_log_to_stream,
    publish_log_to_pubsub,
    log_and_broadcast,
    append_log_to_stream_async,
    publish_log_to_pubsub_async,
    log_and_broadcast_async,
    read_logs_from_stream,
    flush_logs_to_postgres,
    close_thread_redis,
    clear_sequence_counter,
)


class TestMetadataSerialization:
    def test_serialize_metadata_converts_nested_datetimes(self):
        ts = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

        result = _logging._serialize_metadata(
            {"outer": {"ts": ts}, "items": [ts, {"inner": ts}], "plain": "x"}
        )

        assert result == {
            "outer": {"ts": ts.isoformat()},
            "items": [ts.isoformat(), {"inner": ts.isoformat()}],
            "plain": "x",
        }

    def test_clear_sequence_counter_removes_stringified_execution_id(self):
        exec_id = str(uuid4())
        _logging._async_sequence_counters[exec_id] = 3

        clear_sequence_counter(exec_id)

        assert exec_id not in _logging._async_sequence_counters


class TestAppendLogToStream:
    """Tests for sync append_log_to_stream function."""

    def test_append_log_to_stream_success(self):
        """Successfully appends log entry to Redis Stream."""
        exec_id = str(uuid4())

        with patch("bifrost._logging._get_sync_redis") as mock_get_redis:
            mock_redis = MagicMock()
            mock_redis.xadd.return_value = "1234567890-0"
            mock_get_redis.return_value = mock_redis

            entry_id = append_log_to_stream(
                execution_id=exec_id,
                level="INFO",
                message="Test log message",
                metadata={"key": "value"},
            )

            assert entry_id == "1234567890-0"
            mock_redis.xadd.assert_called_once()

            # Verify the call arguments
            call_args = mock_redis.xadd.call_args
            stream_key = call_args[0][0]
            entry = call_args[0][1]

            assert f"bifrost:logs:{exec_id}" == stream_key
            assert entry["execution_id"] == exec_id
            assert entry["level"] == "INFO"
            assert entry["message"] == "Test log message"
            assert json.loads(entry["metadata"]) == {"key": "value"}

    def test_append_log_to_stream_handles_uuid(self):
        """Handles UUID execution_id correctly."""
        exec_uuid = uuid4()

        with patch("bifrost._logging._get_sync_redis") as mock_get_redis:
            mock_redis = MagicMock()
            mock_redis.xadd.return_value = "1234567890-0"
            mock_get_redis.return_value = mock_redis

            entry_id = append_log_to_stream(
                execution_id=exec_uuid,
                level="ERROR",
                message="Error occurred",
            )

            assert entry_id is not None
            call_args = mock_redis.xadd.call_args
            entry = call_args[0][1]
            assert entry["execution_id"] == str(exec_uuid)

    def test_append_log_to_stream_normalizes_level(self):
        """Normalizes log level to uppercase."""
        with patch("bifrost._logging._get_sync_redis") as mock_get_redis:
            mock_redis = MagicMock()
            mock_redis.xadd.return_value = "1234567890-0"
            mock_get_redis.return_value = mock_redis

            append_log_to_stream(
                execution_id="exec-123",
                level="warning",
                message="Warning message",
            )

            call_args = mock_redis.xadd.call_args
            entry = call_args[0][1]
            assert entry["level"] == "WARNING"

    def test_append_log_to_stream_handles_none_metadata(self):
        """Handles None metadata by serializing to empty object."""
        with patch("bifrost._logging._get_sync_redis") as mock_get_redis:
            mock_redis = MagicMock()
            mock_redis.xadd.return_value = "1234567890-0"
            mock_get_redis.return_value = mock_redis

            append_log_to_stream(
                execution_id="exec-123",
                level="INFO",
                message="Message",
                metadata=None,
            )

            call_args = mock_redis.xadd.call_args
            entry = call_args[0][1]
            assert entry["metadata"] == "{}"

    def test_append_log_to_stream_returns_none_on_error(self):
        """Returns None and resets connection on error."""
        with patch("bifrost._logging._get_sync_redis") as mock_get_redis:
            mock_redis = MagicMock()
            mock_redis.xadd.side_effect = Exception("Connection failed")
            mock_get_redis.return_value = mock_redis

            with patch("bifrost._logging._local") as mock_local:
                mock_local.redis = mock_redis

                entry_id = append_log_to_stream(
                    execution_id="exec-123",
                    level="INFO",
                    message="Message",
                )

                assert entry_id is None


class TestPublishLogToPubsub:
    """Tests for sync publish_log_to_pubsub function."""

    def test_publish_log_to_pubsub_success(self):
        """Successfully publishes log to PubSub channel."""
        exec_id = str(uuid4())

        with patch("bifrost._logging._get_sync_redis") as mock_get_redis:
            mock_redis = MagicMock()
            mock_get_redis.return_value = mock_redis

            publish_log_to_pubsub(
                execution_id=exec_id,
                level="INFO",
                message="Test message",
                metadata={"key": "value"},
            )

            mock_redis.publish.assert_called_once()

            call_args = mock_redis.publish.call_args
            channel = call_args[0][0]
            message = json.loads(call_args[0][1])

            assert channel == f"bifrost:execution:{exec_id}"
            assert message["type"] == "execution_log"
            assert message["executionId"] == exec_id
            assert message["level"] == "INFO"
            assert message["message"] == "Test message"
            assert message["metadata"] == {"key": "value"}

    def test_publish_log_to_pubsub_handles_error(self):
        """Handles publish errors gracefully."""
        with patch("bifrost._logging._get_sync_redis") as mock_get_redis:
            mock_redis = MagicMock()
            mock_redis.publish.side_effect = Exception("Publish failed")
            mock_get_redis.return_value = mock_redis

            # Should not raise
            publish_log_to_pubsub(
                execution_id="exec-123",
                level="INFO",
                message="Message",
            )


class TestLogAndBroadcast:
    """Tests for combined log_and_broadcast function."""

    def test_log_and_broadcast_calls_both(self):
        """Calls both stream append and pubsub publish."""
        exec_id = str(uuid4())
        timestamp = datetime.now(timezone.utc)

        with patch("bifrost._logging.append_log_to_stream") as mock_append:
            with patch("bifrost._logging.publish_log_to_pubsub") as mock_publish:
                mock_append.return_value = "entry-id-123"

                entry_id = log_and_broadcast(
                    execution_id=exec_id,
                    level="INFO",
                    message="Test message",
                    metadata={"key": "value"},
                    timestamp=timestamp,
                )

                assert entry_id == "entry-id-123"

                # Both should be called with same timestamp
                mock_append.assert_called_once_with(
                    execution_id=exec_id,
                    level="INFO",
                    message="Test message",
                    metadata={"key": "value"},
                    timestamp=timestamp,
                )
                mock_publish.assert_called_once_with(
                    execution_id=exec_id,
                    level="INFO",
                    message="Test message",
                    metadata={"key": "value"},
                    timestamp=timestamp,
                    sequence=0,
                )

    def test_log_and_broadcast_uses_current_time_if_not_provided(self):
        """Uses current UTC time if timestamp not provided."""
        with patch("bifrost._logging.append_log_to_stream") as mock_append:
            with patch("bifrost._logging.publish_log_to_pubsub") as mock_publish:
                mock_append.return_value = "entry-id"

                log_and_broadcast(
                    execution_id="exec-123",
                    level="INFO",
                    message="Message",
                )

                # Both calls should have same timestamp
                append_ts = mock_append.call_args.kwargs.get("timestamp")
                publish_ts = mock_publish.call_args.kwargs.get("timestamp")

                assert append_ts == publish_ts
                assert append_ts is not None


class TestAsyncLogFunctions:
    """Tests for async log functions."""

    @pytest.mark.asyncio
    async def test_append_log_to_stream_async_success(self):
        """Async version successfully appends to stream."""
        exec_id = str(uuid4())

        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.xadd.return_value = "async-entry-id"
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            entry_id = await append_log_to_stream_async(
                execution_id=exec_id,
                level="INFO",
                message="Async log message",
            )

            assert entry_id == "async-entry-id"

    @pytest.mark.asyncio
    async def test_append_log_to_stream_async_handles_error(self):
        """Async version returns None on error."""
        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.xadd.side_effect = Exception("Async error")
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            entry_id = await append_log_to_stream_async(
                execution_id="exec-123",
                level="INFO",
                message="Message",
            )

            assert entry_id is None

    @pytest.mark.asyncio
    async def test_read_logs_from_stream_success(self):
        """Successfully reads logs from stream."""
        exec_id = str(uuid4())

        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.xrange.return_value = [
                ("1234-0", {
                    "execution_id": exec_id,
                    "level": "INFO",
                    "message": "First log",
                    "metadata": "{}",
                    "timestamp": "2025-01-01T00:00:00",
                }),
                ("1234-1", {
                    "execution_id": exec_id,
                    "level": "ERROR",
                    "message": "Second log",
                    "metadata": '{"error": true}',
                    "timestamp": "2025-01-01T00:00:01",
                }),
            ]
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            logs = await read_logs_from_stream(exec_id)

            assert len(logs) == 2
            assert logs[0].id == "1234-0"
            assert logs[0].level == "INFO"
            assert logs[0].message == "First log"
            assert logs[1].level == "ERROR"
            assert logs[1].metadata == {"error": True}

    @pytest.mark.asyncio
    async def test_read_logs_from_stream_empty(self):
        """Returns empty list when no logs."""
        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.xrange.return_value = []
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            logs = await read_logs_from_stream("exec-123")

            assert logs == []

    @pytest.mark.asyncio
    async def test_read_logs_from_stream_returns_empty_on_error(self):
        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.xrange.side_effect = Exception("redis down")
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            logs = await read_logs_from_stream("exec-123")

            assert logs == []

    @pytest.mark.asyncio
    async def test_publish_log_to_pubsub_async_includes_sequence_and_metadata(self):
        exec_id = str(uuid4())
        timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc)

        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            await publish_log_to_pubsub_async(
                execution_id=exec_id,
                level="warning",
                message="async message",
                metadata={"seen": True},
                timestamp=timestamp,
                sequence=7,
            )

            mock_redis.publish.assert_awaited_once()
            channel, payload = mock_redis.publish.await_args.args
            message = json.loads(payload)
            assert channel == f"bifrost:execution:{exec_id}"
            assert message == {
                "type": "execution_log",
                "executionId": exec_id,
                "level": "WARNING",
                "message": "async message",
                "metadata": {"seen": True},
                "timestamp": timestamp.isoformat(),
                "sequence": 7,
            }

    @pytest.mark.asyncio
    async def test_publish_log_to_pubsub_async_handles_error(self):
        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.publish.side_effect = Exception("publish failed")
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            await publish_log_to_pubsub_async("exec-123", "INFO", "message")

    @pytest.mark.asyncio
    async def test_log_and_broadcast_async_calls_stream_and_pubsub_with_sequence(
        self,
    ):
        exec_id = str(uuid4())
        timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
        _logging._async_sequence_counters.pop(exec_id, None)

        with patch("bifrost._logging.append_log_to_stream_async") as mock_append:
            with patch("bifrost._logging.publish_log_to_pubsub_async") as mock_publish:
                mock_append.return_value = "entry-1"

                entry_id = await log_and_broadcast_async(
                    exec_id,
                    "INFO",
                    "message",
                    metadata={"x": 1},
                    timestamp=timestamp,
                )

        assert entry_id == "entry-1"
        mock_append.assert_awaited_once_with(
            execution_id=exec_id,
            level="INFO",
            message="message",
            metadata={"x": 1},
            timestamp=timestamp,
        )
        mock_publish.assert_awaited_once_with(
            execution_id=exec_id,
            level="INFO",
            message="message",
            metadata={"x": 1},
            timestamp=timestamp,
            sequence=0,
        )
        assert _logging._async_sequence_counters[exec_id] == 1


class TestFlushLogsToPostgres:
    """Tests for flush_logs_to_postgres function."""

    @pytest.mark.asyncio
    async def test_flush_logs_returns_zero_when_no_entries(self):
        """Returns 0 when stream is empty."""
        exec_id = str(uuid4())  # Use valid UUID

        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.xrange.return_value = []
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            count = await flush_logs_to_postgres(exec_id)

            assert count == 0

    @pytest.mark.asyncio
    async def test_flush_logs_persists_entries(self):
        """Successfully persists entries and clears stream."""
        exec_id = str(uuid4())

        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.xrange.return_value = [
                ("1234-0", {
                    "level": "INFO",
                    "message": "Log 1",
                    "metadata": "{}",
                    "timestamp": "2025-01-01T00:00:00",
                }),
                ("1234-1", {
                    "level": "INFO",
                    "message": "Log 2",
                    "metadata": "{}",
                    "timestamp": "2025-01-01T00:00:01",
                }),
            ]
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            # Patch at the location where it's used, not where it's defined
            with patch("src.core.database.get_session_factory") as mock_session_factory:
                mock_db = MagicMock()
                mock_db.commit = AsyncMock()  # commit is async
                mock_session_factory.return_value.return_value.__aenter__.return_value = mock_db

                count = await flush_logs_to_postgres(exec_id)

                assert count == 2
                mock_db.add_all.assert_called_once()
                mock_db.commit.assert_called_once()
                mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_flush_logs_with_provided_session_skips_bad_entries(self):
        exec_id = str(uuid4())
        session = MagicMock()

        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.xrange.return_value = [
                (
                    "bad-0",
                    {
                        "level": "INFO",
                        "message": "Bad log",
                        "metadata": "{bad json",
                        "timestamp": "2025-01-01T00:00:00",
                    },
                ),
                (
                    "good-0",
                    {
                        "level": "ERROR",
                        "message": "Good log",
                        "metadata": '{"ok": true}',
                        "timestamp": "2025-01-01T00:00:01+00:00",
                    },
                ),
            ]
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            count = await flush_logs_to_postgres(exec_id, session=session)

        assert count == 1
        session.add_all.assert_called_once()
        added = session.add_all.call_args.args[0]
        assert len(added) == 1
        assert added[0].message == "Good log"
        assert added[0].timestamp.tzinfo is None
        mock_redis.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_flush_logs_returns_zero_when_all_entries_fail_to_parse(self):
        exec_id = str(uuid4())
        session = MagicMock()

        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.xrange.return_value = [
                ("bad-0", {"metadata": "{bad json", "timestamp": "not-a-date"}),
            ]
            mock_get_redis.return_value.__aenter__.return_value = mock_redis

            count = await flush_logs_to_postgres(exec_id, session=session)

        assert count == 0
        session.add_all.assert_not_called()
        mock_redis.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_flush_logs_returns_zero_on_outer_error(self):
        exec_id = str(uuid4())

        with patch("src.core.cache.get_redis") as mock_get_redis:
            mock_get_redis.return_value.__aenter__.side_effect = Exception("redis down")

            count = await flush_logs_to_postgres(exec_id)

        assert count == 0


class TestCloseThreadRedis:
    """Tests for close_thread_redis function."""

    def test_close_thread_redis_with_connection(self):
        """Closes connection when one exists."""
        with patch("bifrost._logging._local") as mock_local:
            mock_redis = MagicMock()
            mock_local.redis = mock_redis

            close_thread_redis()

            mock_redis.close.assert_called_once()

    def test_close_thread_redis_without_connection(self):
        """Does nothing when no connection exists."""
        with patch("bifrost._logging._local") as mock_local:
            mock_local.redis = None

            # Should not raise
            close_thread_redis()

    def test_close_thread_redis_ignores_close_error_and_clears_sequence_counters(self):
        with patch("bifrost._logging._local") as mock_local:
            mock_redis = MagicMock()
            mock_redis.close.side_effect = Exception("already closed")
            mock_local.redis = mock_redis
            mock_local.sequence_counters = {"exec": 4}

            close_thread_redis()

            mock_redis.close.assert_called_once()
            assert mock_local.redis is None
            assert mock_local.sequence_counters == {}


class TestSyncRedisConnection:
    def test_get_sync_redis_reuses_thread_local_connection(self):
        with patch("bifrost._logging._local") as mock_local:
            mock_redis = MagicMock()
            mock_local.redis = mock_redis

            assert _logging._get_sync_redis() is mock_redis

    def test_get_sync_redis_creates_connection_from_settings(self):
        with patch("bifrost._logging._local") as mock_local:
            if hasattr(mock_local, "redis"):
                del mock_local.redis
            with patch("src.config.get_settings") as mock_settings:
                with patch("bifrost._logging.redis_sync.from_url") as mock_from_url:
                    mock_settings.return_value.redis_url = "redis://example"
                    mock_redis = MagicMock()
                    mock_from_url.return_value = mock_redis

                    assert _logging._get_sync_redis() is mock_redis

        mock_from_url.assert_called_once_with("redis://example", decode_responses=True)
