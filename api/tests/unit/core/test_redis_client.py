"""
Unit tests for Redis client for sync execution results.

Tests the BLPOP/RPUSH pattern for synchronous workflow execution.
"""

import pytest
import json
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock


class TestRedisClient:
    """Tests for RedisClient."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis instance."""
        redis = AsyncMock()
        redis.get = AsyncMock()
        redis.delete = AsyncMock()
        redis.publish = AsyncMock()
        redis.rpush = AsyncMock()
        redis.expire = AsyncMock()
        redis.blpop = AsyncMock()
        redis.setex = AsyncMock()
        redis.ttl = AsyncMock()
        redis.scan = AsyncMock()
        redis.close = AsyncMock()
        return redis

    async def test_push_result_success(self, mock_redis):
        """Test pushing execution result to Redis."""
        from src.core.redis_client import RedisClient, RESULT_KEY_PREFIX, RESULT_TTL_SECONDS

        client = RedisClient()
        client._redis = mock_redis

        await client.push_result(
            execution_id="exec-123",
            status="Success",
            result={"data": "test"},
            duration_ms=150,
        )

        expected_key = f"{RESULT_KEY_PREFIX}exec-123"
        mock_redis.rpush.assert_called_once()
        call_args = mock_redis.rpush.call_args
        assert call_args[0][0] == expected_key

        # Verify payload
        payload = json.loads(call_args[0][1])
        assert payload["status"] == "Success"
        assert payload["result"] == {"data": "test"}
        assert payload["duration_ms"] == 150

        # Verify TTL was set
        mock_redis.expire.assert_called_once_with(expected_key, RESULT_TTL_SECONDS)

    async def test_push_result_with_error(self, mock_redis):
        """Test pushing error result to Redis."""
        from src.core.redis_client import RedisClient

        client = RedisClient()
        client._redis = mock_redis

        await client.push_result(
            execution_id="exec-456",
            status="Failed",
            error="Something went wrong",
            error_type="RuntimeError",
            duration_ms=50,
        )

        call_args = mock_redis.rpush.call_args
        payload = json.loads(call_args[0][1])
        assert payload["status"] == "Failed"
        assert payload["error"] == "Something went wrong"
        assert payload["error_type"] == "RuntimeError"

    async def test_push_result_serializes_non_json_values(self, mock_redis):
        """Sync result payloads should stringify values like Decimal."""
        from src.core.redis_client import RedisClient

        client = RedisClient()
        client._redis = mock_redis

        await client.push_result(
            execution_id="exec-decimal",
            status="Success",
            result={"value": Decimal("12.34")},
        )

        payload = json.loads(mock_redis.rpush.call_args.args[1])
        assert payload["result"] == {"value": "12.34"}

    async def test_push_result_errors_are_not_suppressed(self, mock_redis):
        """Sync result writes are required for blocking callers."""
        from src.core.redis_client import RedisClient

        mock_redis.rpush.side_effect = RuntimeError("rpush failed")
        client = RedisClient()
        client._redis = mock_redis

        with pytest.raises(RuntimeError, match="rpush failed"):
            await client.push_result("exec", "Failed")

    async def test_set_pending_execution_stores_full_payload(self, mock_redis):
        """Pending executions should be serialized with all worker context."""
        from src.core.redis_client import (
            PENDING_EXECUTION_TTL_SECONDS,
            PENDING_KEY_PREFIX,
            PENDING_KEY_SUFFIX,
            RedisClient,
        )

        client = RedisClient()
        client._redis = mock_redis

        await client.set_pending_execution(
            execution_id="exec-1",
            workflow_id="wf-1",
            script_name="inline.py",
            parameters={"answer": 42},
            org_id="ORG:abc",
            user_id="user-1",
            user_name="Ada",
            user_email="ada@example.com",
            form_id="form-1",
            api_key_id="api-wf-1",
            startup={"ready": True},
            sync=True,
            is_platform_admin=True,
            event={"kind": "webhook"},
        )

        key = f"{PENDING_KEY_PREFIX}exec-1{PENDING_KEY_SUFFIX}"
        mock_redis.setex.assert_called_once()
        assert mock_redis.setex.call_args.args[0] == key
        assert mock_redis.setex.call_args.args[1] == PENDING_EXECUTION_TTL_SECONDS
        payload = json.loads(mock_redis.setex.call_args.args[2])
        assert payload["execution_id"] == "exec-1"
        assert payload["workflow_id"] == "wf-1"
        assert payload["script_name"] == "inline.py"
        assert payload["parameters"] == {"answer": 42}
        assert payload["sync"] is True
        assert payload["is_platform_admin"] is True
        assert payload["event"] == {"kind": "webhook"}
        assert payload["cancelled"] is False
        assert payload["created_at"]

    async def test_pending_execution_write_errors_are_not_suppressed(
        self, mock_redis
    ):
        """Pending execution writes are required state and must fail loudly."""
        from src.core.redis_client import RedisClient

        mock_redis.setex.side_effect = RuntimeError("redis write failed")
        client = RedisClient()
        client._redis = mock_redis

        with pytest.raises(RuntimeError, match="redis write failed"):
            await client.set_pending_execution(
                execution_id="exec-write-fail",
                workflow_id="wf",
                parameters={},
                org_id=None,
                user_id="user",
                user_name="Ada",
                user_email="ada@example.com",
            )

    async def test_get_pending_execution_returns_payload(self, mock_redis):
        """Pending executions should be deserialized from Redis."""
        from src.core.redis_client import RedisClient

        pending = {"execution_id": "exec-2", "cancelled": False}
        mock_redis.get.return_value = json.dumps(pending)
        client = RedisClient()
        client._redis = mock_redis

        result = await client.get_pending_execution("exec-2")

        assert result == pending

    async def test_get_pending_execution_missing_returns_none(self, mock_redis):
        """Missing pending executions should not raise."""
        from src.core.redis_client import RedisClient

        mock_redis.get.return_value = None
        client = RedisClient()
        client._redis = mock_redis

        assert await client.get_pending_execution("missing") is None

    async def test_delete_pending_execution_deletes_expected_key(self, mock_redis):
        """Deleting pending execution should remove the exact pending key."""
        from src.core.redis_client import PENDING_KEY_PREFIX, PENDING_KEY_SUFFIX, RedisClient

        client = RedisClient()
        client._redis = mock_redis

        await client.delete_pending_execution("exec-delete")

        mock_redis.delete.assert_called_once_with(
            f"{PENDING_KEY_PREFIX}exec-delete{PENDING_KEY_SUFFIX}"
        )

    async def test_pending_execution_delete_errors_are_not_suppressed(
        self, mock_redis
    ):
        """Worker cleanup failures should surface to the caller."""
        from src.core.redis_client import RedisClient

        mock_redis.delete.side_effect = RuntimeError("delete failed")
        client = RedisClient()
        client._redis = mock_redis

        with pytest.raises(RuntimeError, match="delete failed"):
            await client.delete_pending_execution("exec-delete")

    async def test_set_pending_cancelled_preserves_positive_ttl(self, mock_redis):
        """Cancelling a pending execution should preserve its remaining TTL."""
        from src.core.redis_client import PENDING_KEY_PREFIX, PENDING_KEY_SUFFIX, RedisClient

        mock_redis.get.return_value = json.dumps(
            {"execution_id": "exec-cancel", "cancelled": False, "value": 1}
        )
        mock_redis.ttl.return_value = 25
        client = RedisClient()
        client._redis = mock_redis

        assert await client.set_pending_cancelled("exec-cancel") is True

        key = f"{PENDING_KEY_PREFIX}exec-cancel{PENDING_KEY_SUFFIX}"
        assert mock_redis.setex.call_args.args[0] == key
        assert mock_redis.setex.call_args.args[1] == 25
        payload = json.loads(mock_redis.setex.call_args.args[2])
        assert payload["cancelled"] is True
        assert payload["value"] == 1

    async def test_set_pending_cancelled_restores_default_ttl(self, mock_redis):
        """Cancelling should use the default TTL when Redis reports no TTL."""
        from src.core.redis_client import PENDING_EXECUTION_TTL_SECONDS, RedisClient

        mock_redis.get.return_value = json.dumps({"execution_id": "exec-cancel"})
        mock_redis.ttl.return_value = -1
        client = RedisClient()
        client._redis = mock_redis

        assert await client.set_pending_cancelled("exec-cancel") is True

        assert mock_redis.setex.call_args.args[1] == PENDING_EXECUTION_TTL_SECONDS

    async def test_set_pending_cancelled_missing_returns_false(self, mock_redis):
        """Cancelling a missing pending execution should return false."""
        from src.core.redis_client import RedisClient

        mock_redis.get.return_value = None
        client = RedisClient()
        client._redis = mock_redis

        assert await client.set_pending_cancelled("missing") is False
        mock_redis.setex.assert_not_called()

    async def test_is_pending_cancelled_reads_pending_flag(self, mock_redis):
        """Cancellation checks should reflect the stored pending flag."""
        from src.core.redis_client import RedisClient

        mock_redis.get.return_value = json.dumps({"execution_id": "exec", "cancelled": True})
        client = RedisClient()
        client._redis = mock_redis

        assert await client.is_pending_cancelled("exec") is True

    async def test_update_pending_execution_merges_updates(self, mock_redis):
        """Pending execution updates should merge fields and preserve TTL."""
        from src.core.redis_client import RedisClient

        mock_redis.get.return_value = json.dumps({"execution_id": "exec", "a": 1})
        mock_redis.ttl.return_value = 44
        client = RedisClient()
        client._redis = mock_redis

        assert await client.update_pending_execution("exec", {"b": 2}) is True

        assert mock_redis.setex.call_args.args[1] == 44
        assert json.loads(mock_redis.setex.call_args.args[2]) == {
            "execution_id": "exec",
            "a": 1,
            "b": 2,
        }

    async def test_update_pending_execution_missing_returns_false(self, mock_redis):
        """Updating a missing pending execution should not create it."""
        from src.core.redis_client import RedisClient

        mock_redis.get.return_value = None
        client = RedisClient()
        client._redis = mock_redis

        assert await client.update_pending_execution("missing", {"b": 2}) is False
        mock_redis.setex.assert_not_called()

    async def test_update_pending_execution_restores_default_ttl_when_absent(
        self, mock_redis
    ):
        """Pending updates should restore safety TTL if Redis lost expiry."""
        from src.core.redis_client import PENDING_EXECUTION_TTL_SECONDS, RedisClient

        mock_redis.get.return_value = json.dumps({"execution_id": "exec", "a": 1})
        mock_redis.ttl.return_value = 0
        client = RedisClient()
        client._redis = mock_redis

        assert await client.update_pending_execution("exec", {"b": 2}) is True

        assert mock_redis.setex.call_args.args[1] == PENDING_EXECUTION_TTL_SECONDS
        assert json.loads(mock_redis.setex.call_args.args[2]) == {
            "execution_id": "exec",
            "a": 1,
            "b": 2,
        }

    async def test_wait_for_result_success(self, mock_redis):
        """Test waiting for execution result from Redis."""
        from src.core.redis_client import RedisClient, RESULT_KEY_PREFIX

        expected_result = {"status": "Success", "result": {"data": "test"}}
        mock_redis.blpop.return_value = (
            f"{RESULT_KEY_PREFIX}exec-789",
            json.dumps(expected_result),
        )

        client = RedisClient()
        client._redis = mock_redis

        result = await client.wait_for_result(
            execution_id="exec-789",
            timeout_seconds=30,
        )

        assert result == expected_result
        mock_redis.blpop.assert_called_once()

    async def test_wait_for_result_timeout(self, mock_redis):
        """Test timeout when waiting for result."""
        from src.core.redis_client import RedisClient

        mock_redis.blpop.return_value = None  # Timeout

        client = RedisClient()
        client._redis = mock_redis

        result = await client.wait_for_result(
            execution_id="exec-timeout",
            timeout_seconds=5,
        )

        assert result is None

    async def test_wait_for_result_errors_are_not_suppressed(self, mock_redis):
        """BLPOP failures should surface because sync callers need a real result."""
        from src.core.redis_client import RedisClient

        mock_redis.blpop.side_effect = RuntimeError("blpop failed")
        client = RedisClient()
        client._redis = mock_redis

        with pytest.raises(RuntimeError, match="blpop failed"):
            await client.wait_for_result("exec")

    async def test_set_agent_run_cancel_flag(self, mock_redis):
        """Agent run cancel flags should use the agent-run key namespace."""
        from src.core.redis_client import RedisClient

        client = RedisClient()
        client._redis = mock_redis

        await client.set_agent_run_cancel_flag("run-1")

        mock_redis.setex.assert_called_once_with(
            "bifrost:agent_run:run-1:cancel", 3600, "1"
        )

    async def test_set_agent_run_cancel_flag_errors_are_not_suppressed(
        self, mock_redis
    ):
        """Failure to set an agent cancel flag must be visible to callers."""
        from src.core.redis_client import RedisClient

        mock_redis.setex.side_effect = RuntimeError("setex failed")
        client = RedisClient()
        client._redis = mock_redis

        with pytest.raises(RuntimeError, match="setex failed"):
            await client.set_agent_run_cancel_flag("run-1")

    async def test_check_agent_run_cancel_flag(self, mock_redis):
        """Agent run cancel checks should return true when a key exists."""
        from src.core.redis_client import RedisClient

        mock_redis.get.return_value = "1"
        client = RedisClient()
        client._redis = mock_redis

        assert await client.check_agent_run_cancel_flag("run-1") is True

    async def test_check_agent_run_cancel_flag_handles_redis_error(self, mock_redis):
        """Agent run cancel checks should fail closed on Redis read errors."""
        from src.core.redis_client import RedisClient

        mock_redis.get.side_effect = RuntimeError("redis down")
        client = RedisClient()
        client._redis = mock_redis

        assert await client.check_agent_run_cancel_flag("run-1") is False

    async def test_set_cancel_flag(self, mock_redis):
        """Execution cancel flags should use the execution key namespace."""
        from src.core.redis_client import RedisClient

        client = RedisClient()
        client._redis = mock_redis

        await client.set_cancel_flag("exec-1")

        mock_redis.setex.assert_called_once_with("bifrost:exec:exec-1:cancel", 3600, "1")

    async def test_set_cancel_flag_errors_are_not_suppressed(self, mock_redis):
        """Failure to set an execution cancel flag must be visible to callers."""
        from src.core.redis_client import RedisClient

        mock_redis.setex.side_effect = RuntimeError("setex failed")
        client = RedisClient()
        client._redis = mock_redis

        with pytest.raises(RuntimeError, match="setex failed"):
            await client.set_cancel_flag("exec-1")

    async def test_publish_cancel_event_publishes_json(self, mock_redis):
        """Cancel events should publish execution_id payloads."""
        from src.core.redis_client import RedisClient

        client = RedisClient()
        client._redis = mock_redis

        await client.publish_cancel_event("exec-1")

        mock_redis.publish.assert_called_once_with(
            "bifrost:cancel", json.dumps({"execution_id": "exec-1"})
        )

    async def test_publish_cancel_event_swallows_publish_error(self, mock_redis):
        """Publishing is best-effort because cancel flags are the fallback."""
        from src.core.redis_client import RedisClient

        mock_redis.publish.side_effect = RuntimeError("pubsub down")
        client = RedisClient()
        client._redis = mock_redis

        await client.publish_cancel_event("exec-1")

    async def test_close(self, mock_redis):
        """Test closing Redis connection."""
        from src.core.redis_client import RedisClient

        client = RedisClient()
        client._redis = mock_redis

        await client.close()

        mock_redis.close.assert_called_once()
        assert client._redis is None

    async def test_set_workflow_metadata_cache_serializes_decimal(self, mock_redis):
        """Workflow metadata cache should coerce Decimal values to JSON numbers."""
        from src.core.redis_client import RedisClient, WORKFLOW_METADATA_CACHE_PREFIX

        client = RedisClient()
        client._redis = mock_redis

        await client.set_workflow_metadata_cache(
            workflow_id="wf-123",
            name="Test Workflow",
            file_path="features/test/workflows/example.py",
            timeout_seconds=300,
            time_saved=15,
            value=cast(float, Decimal("42.50")),
            execution_mode="sync",
        )

        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == f"{WORKFLOW_METADATA_CACHE_PREFIX}wf-123"
        payload = json.loads(call_args[0][2])
        assert payload["value"] == 42.5

    async def test_endpoint_workflow_cache_roundtrip_helpers(self, mock_redis):
        """Endpoint workflow cache helpers should serialize and deserialize metadata."""
        from src.core.redis_client import (
            ENDPOINT_WORKFLOW_CACHE_PREFIX,
            ENDPOINT_WORKFLOW_CACHE_TTL_SECONDS,
            RedisClient,
        )

        client = RedisClient()
        client._redis = mock_redis

        await client.set_endpoint_workflow_cache(
            workflow_id="wf-ep",
            file_path="workflows/example.py",
            execution_mode="sync",
            timeout_seconds=30,
            allowed_methods=["POST"],
        )

        key = f"{ENDPOINT_WORKFLOW_CACHE_PREFIX}wf-ep"
        mock_redis.setex.assert_called_once()
        assert mock_redis.setex.call_args.args[0] == key
        assert mock_redis.setex.call_args.args[1] == ENDPOINT_WORKFLOW_CACHE_TTL_SECONDS
        cached = json.loads(mock_redis.setex.call_args.args[2])
        assert cached == {
            "workflow_id": "wf-ep",
            "file_path": "workflows/example.py",
            "execution_mode": "sync",
            "timeout_seconds": 30,
            "allowed_methods": ["POST"],
        }

        mock_redis.get.return_value = json.dumps(cached)
        assert await client.get_endpoint_workflow_cache("wf-ep") == cached

    async def test_endpoint_workflow_cache_miss_and_error_return_none(self, mock_redis):
        """Endpoint cache reads should degrade to a miss on absent or invalid data."""
        from src.core.redis_client import RedisClient

        client = RedisClient()
        client._redis = mock_redis

        mock_redis.get.return_value = None
        assert await client.get_endpoint_workflow_cache("missing") is None

        mock_redis.get.return_value = "{"
        assert await client.get_endpoint_workflow_cache("bad-json") is None

    async def test_endpoint_workflow_cache_invalidation(self, mock_redis):
        """Endpoint cache invalidation should delete the exact workflow key."""
        from src.core.redis_client import ENDPOINT_WORKFLOW_CACHE_PREFIX, RedisClient

        client = RedisClient()
        client._redis = mock_redis

        await client.invalidate_endpoint_workflow_cache("wf-ep")

        mock_redis.delete.assert_called_once_with(f"{ENDPOINT_WORKFLOW_CACHE_PREFIX}wf-ep")

    async def test_endpoint_cache_write_and_invalidate_errors_are_best_effort(
        self, mock_redis
    ):
        """Endpoint cache failures should not fail endpoint requests."""
        from src.core.redis_client import RedisClient

        client = RedisClient()
        client._redis = mock_redis

        mock_redis.setex.side_effect = RuntimeError("cache write failed")
        await client.set_endpoint_workflow_cache(
            workflow_id="wf-ep",
            file_path="workflows/example.py",
            execution_mode="sync",
            timeout_seconds=30,
            allowed_methods=["POST"],
        )

        mock_redis.setex.side_effect = None
        mock_redis.delete.side_effect = RuntimeError("cache delete failed")
        await client.invalidate_endpoint_workflow_cache("wf-ep")

    async def test_invalidate_all_endpoint_workflow_caches_deletes_scan_pages(
        self, mock_redis
    ):
        """Bulk endpoint cache invalidation should scan until Redis cursor zero."""
        from src.core.redis_client import ENDPOINT_WORKFLOW_CACHE_PREFIX, RedisClient

        mock_redis.scan.side_effect = [
            (10, [f"{ENDPOINT_WORKFLOW_CACHE_PREFIX}one"]),
            (
                0,
                [
                    f"{ENDPOINT_WORKFLOW_CACHE_PREFIX}two",
                    f"{ENDPOINT_WORKFLOW_CACHE_PREFIX}three",
                ],
            ),
        ]
        client = RedisClient()
        client._redis = mock_redis

        assert await client.invalidate_all_endpoint_workflow_caches() == 3
        assert mock_redis.delete.call_count == 2

    async def test_invalidate_all_endpoint_workflow_caches_handles_scan_error(
        self, mock_redis
    ):
        """Bulk endpoint invalidation should return zero on Redis scan errors."""
        from src.core.redis_client import RedisClient

        mock_redis.scan.side_effect = RuntimeError("scan down")
        client = RedisClient()
        client._redis = mock_redis

        assert await client.invalidate_all_endpoint_workflow_caches() == 0

    async def test_workflow_metadata_cache_read_miss_and_error(self, mock_redis):
        """Workflow metadata cache reads should handle misses and malformed payloads."""
        from src.core.redis_client import RedisClient

        client = RedisClient()
        client._redis = mock_redis

        mock_redis.get.return_value = None
        assert await client.get_workflow_metadata_cache("wf") is None

        mock_redis.get.return_value = "{"
        assert await client.get_workflow_metadata_cache("wf") is None

    async def test_workflow_metadata_cache_invalidation(self, mock_redis):
        """Workflow metadata invalidation should delete the workflow metadata key."""
        from src.core.redis_client import RedisClient, WORKFLOW_METADATA_CACHE_PREFIX

        client = RedisClient()
        client._redis = mock_redis

        await client.invalidate_workflow_metadata_cache("wf-meta")

        mock_redis.delete.assert_called_once_with(f"{WORKFLOW_METADATA_CACHE_PREFIX}wf-meta")

    async def test_workflow_metadata_cache_write_and_invalidate_errors_are_best_effort(
        self, mock_redis
    ):
        """Execution metadata cache failures should not fail workflow execution."""
        from src.core.redis_client import RedisClient

        client = RedisClient()
        client._redis = mock_redis

        mock_redis.setex.side_effect = RuntimeError("metadata cache down")
        await client.set_workflow_metadata_cache(
            workflow_id="wf-meta",
            name="Workflow",
            file_path="workflows/example.py",
            timeout_seconds=30,
            time_saved=5,
            value=1.5,
            execution_mode="sync",
        )

        mock_redis.setex.side_effect = None
        mock_redis.delete.side_effect = RuntimeError("metadata delete failed")
        await client.invalidate_workflow_metadata_cache("wf-meta")

    async def test_general_get_setex_delete_scan_delegate_to_redis(self, mock_redis):
        """General-purpose helpers should delegate directly to Redis."""
        from src.core.redis_client import RedisClient

        mock_redis.get.return_value = "value"
        mock_redis.delete.return_value = 2
        mock_redis.scan.return_value = (0, ["a"])
        client = RedisClient()
        client._redis = mock_redis

        get_result = await client.get("key")
        await client.setex("key", 10, "value")
        delete_result = await client.delete("key")
        scan_result = await client.scan(0, match="prefix:*", count=25)

        assert get_result == "value"
        assert delete_result == 2
        assert scan_result == (0, ["a"])

        mock_redis.setex.assert_called_with("key", 10, "value")
        mock_redis.scan.assert_called_with(0, match="prefix:*", count=25)


class TestRedisClientSingleton:
    """Tests for Redis client singleton functions."""

    def test_get_redis_client_returns_singleton(self):
        """Test that get_redis_client returns same instance."""
        from src.core.redis_client import get_redis_client

        # Reset singleton
        import src.core.redis_client as module
        module._redis_client = None

        client1 = get_redis_client()
        client2 = get_redis_client()

        assert client1 is client2

    async def test_close_redis_client(self):
        """Test closing singleton Redis client."""
        from src.core.redis_client import close_redis_client, get_redis_client
        import src.core.redis_client as module

        # Reset singleton
        module._redis_client = None

        get_redis_client()  # Creates singleton
        assert module._redis_client is not None

        # Close without connecting
        await close_redis_client()
        assert module._redis_client is None
