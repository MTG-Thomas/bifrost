import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.models.contracts.platform import RecycleAllRequest, RecycleProcessRequest
from src.routers.platform import workers


class _FakeRedis:
    def __init__(self):
        self.scan_results = [(0, [])]
        self.hashes: dict[str, dict[str, str]] = {}
        self.values: dict[str, str] = {}
        self.exists_values: dict[str, int] = {}
        self.published: list[tuple[str, str]] = []

    async def scan(self, cursor, match=None, count=None):
        return self.scan_results.pop(0)

    async def get(self, key):
        return self.values.get(key)

    async def hgetall(self, key):
        return self.hashes.get(key, {})

    async def exists(self, key):
        return self.exists_values.get(key, 0)

    async def publish(self, channel, payload):
        self.published.append((channel, payload))
        return 1


def _admin():
    return SimpleNamespace(user_id="admin-1")


@pytest.mark.asyncio
async def test_get_pool_stats_aggregates_known_capacity_and_skips_bad_heartbeat():
    redis = _FakeRedis()
    redis.scan_results = [
        (
            0,
            [
                "bifrost:pool:worker-a",
                "bifrost:pool:worker-a:heartbeat",
                "bifrost:pool:worker-b",
                "bifrost:pool:not:a:pool",
            ],
        )
    ]
    redis.values = {
        "bifrost:pool:worker-a:heartbeat": json.dumps(
            {
                "active_process_count": 2,
                "configured_capacity": "4",
                "idle_count": 1,
                "busy_count": 1,
            }
        ),
        "bifrost:pool:worker-b:heartbeat": "{bad-json",
    }

    with patch.object(workers, "_get_redis", AsyncMock(return_value=redis)):
        result = await workers.get_pool_stats(_admin())

    assert result.total_pools == 3
    assert result.total_processes == 2
    assert result.total_configured_capacity == 4
    assert result.total_idle == 1
    assert result.total_busy == 1


@pytest.mark.asyncio
async def test_get_pool_stats_hides_capacity_when_any_heartbeat_omits_capacity():
    redis = _FakeRedis()
    redis.scan_results = [(0, ["bifrost:pool:worker-a", "bifrost:pool:worker-b"])]
    redis.values = {
        "bifrost:pool:worker-a:heartbeat": json.dumps(
            {"active_process_count": 1, "configured_capacity": 3}
        ),
        "bifrost:pool:worker-b:heartbeat": json.dumps(
            {"active_process_count": 2, "busy_count": 2}
        ),
    }

    with patch.object(workers, "_get_redis", AsyncMock(return_value=redis)):
        result = await workers.get_pool_stats(_admin())

    assert result.total_pools == 2
    assert result.total_processes == 3
    assert result.total_configured_capacity is None


@pytest.mark.asyncio
async def test_list_pools_merges_registration_and_heartbeat_runtime_fields():
    redis = _FakeRedis()
    redis.scan_results = [
        (
            0,
            [
                "bifrost:pool:worker-a",
                "bifrost:pool:worker-a:heartbeat",
                "bifrost:pool:worker-b",
            ],
        )
    ]
    redis.hashes = {
        "bifrost:pool:worker-a": {
            "hostname": "node-a",
            "runtime": "old",
            "runtime_label": "Old runtime",
            "status": "online",
            "started_at": "2026-07-05T00:00:00Z",
        },
        "bifrost:pool:worker-b": {"hostname": "node-b"},
    }
    redis.values = {
        "bifrost:pool:worker-a:heartbeat": json.dumps(
            {
                "timestamp": "2026-07-05T01:00:00Z",
                "pool_size": 3,
                "active_process_count": 2,
                "configured_capacity": 5,
                "max_workers": 6,
                "idle_count": 1,
                "busy_count": 1,
                "runtime": "python",
                "requirements_installed": 7,
                "requirements_total": 8,
                "memory_current_bytes": 10,
                "memory_max_bytes": 100,
            }
        ),
        "bifrost:pool:worker-b:heartbeat": "{bad-json",
    }

    with patch.object(workers, "_get_redis", AsyncMock(return_value=redis)):
        result = await workers.list_pools(_admin())

    assert result.total == 2
    first = result.pools[0]
    assert first.worker_id == "worker-a"
    assert first.hostname == "node-a"
    assert first.runtime == "python"
    assert first.runtime_label is None
    assert first.pool_size == 3
    assert first.active_process_count == 2
    assert first.configured_capacity == 5
    assert first.max_workers == 6
    assert first.requirements_installed == 7
    assert result.pools[1].worker_id == "worker-b"


@pytest.mark.asyncio
async def test_get_pool_details_parses_processes_and_raises_for_missing_pool():
    redis = _FakeRedis()
    redis.exists_values = {"bifrost:pool:worker-a": 1}
    redis.hashes = {
        "bifrost:pool:worker-a": {
            "hostname": "node-a",
            "runtime": "old",
            "runtime_label": "Old runtime",
            "status": "online",
        }
    }
    redis.values = {
        "bifrost:pool:worker-a:heartbeat": json.dumps(
            {
                "timestamp": "now",
                "configured_capacity": 2,
                "runtime": "python",
                "processes": [
                    {
                        "process_id": "process-1",
                        "pid": 123,
                        "state": "busy",
                        "execution": {"execution_id": "exec-1"},
                        "executions_completed": 4,
                        "uptime_seconds": 9.5,
                        "memory_mb": 64,
                    }
                ],
            }
        )
    }

    with patch.object(workers, "_get_redis", AsyncMock(return_value=redis)):
        result = await workers.get_pool("worker-a", _admin())

    assert result.worker_id == "worker-a"
    assert result.runtime == "python"
    assert result.runtime_label is None
    assert result.processes[0].process_id == "process-1"
    assert result.processes[0].current_execution_id == "exec-1"
    assert result.processes[0].is_alive is True

    with patch.object(workers, "_get_redis", AsyncMock(return_value=_FakeRedis())):
        with pytest.raises(HTTPException) as exc:
            await workers.get_pool("missing", _admin())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_recycle_process_and_all_publish_commands():
    redis = _FakeRedis()
    redis.exists_values = {"bifrost:pool:worker-a": 1}
    redis.values = {
        "bifrost:pool:worker-a:heartbeat": json.dumps({"pool_size": 3})
    }

    with patch.object(workers, "_get_redis", AsyncMock(return_value=redis)):
        process_result = await workers.recycle_process(
            "worker-a",
            123,
            _admin(),
            RecycleProcessRequest(reason="leak test"),
        )
        all_result = await workers.recycle_all_processes(
            "worker-a",
            _admin(),
            RecycleAllRequest(reason="operator"),
        )

    assert process_result.success is True
    assert all_result.processes_affected == 3
    process_command = json.loads(redis.published[0][1])
    all_command = json.loads(redis.published[1][1])
    assert redis.published[0][0] == "bifrost:pool:worker-a:commands"
    assert process_command["action"] == "recycle_process"
    assert process_command["pid"] == 123
    assert process_command["reason"] == "leak test"
    assert all_command["action"] == "recycle_all"
    assert all_command["reason"] == "operator"
