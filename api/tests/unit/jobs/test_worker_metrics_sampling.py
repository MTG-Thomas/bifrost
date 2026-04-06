"""Tests for the worker metrics sampling scheduler job."""

import json
from unittest.mock import patch


class _FakeRedis:
    def __init__(self, entries: dict[str, str]):
        self._entries = entries

    async def scan(self, cursor, match=None, count=100):
        return 0, list(self._entries.keys())

    async def get(self, key):
        return self._entries.get(key)


class _CapturingSession:
    def __init__(self):
        self.added: list = []
        self.committed = False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _session_factory_for(session: _CapturingSession):
    def _factory():
        return session

    return _factory


async def _run_with_heartbeat(heartbeat: dict) -> _CapturingSession:
    from src.jobs.schedulers import worker_metrics_sampling

    redis = _FakeRedis({"bifrost:pool:w1:heartbeat": json.dumps(heartbeat)})
    session = _CapturingSession()

    with (
        patch.object(worker_metrics_sampling, "get_redis_client", return_value=redis),
        patch.object(
            worker_metrics_sampling,
            "get_session_factory",
            return_value=_session_factory_for(session),
        ),
    ):
        result = await worker_metrics_sampling.sample_worker_metrics()
    assert result.get("error") is None
    return session


async def test_sampler_persists_row_when_memory_max_unknown():
    """memory_max=-1 should persist as NULL, not skip the row."""
    session = await _run_with_heartbeat(
        {
            "worker_id": "w1",
            "memory_current_bytes": 300_000_000,
            "memory_max_bytes": -1,
            "pool_size": 2,
            "busy_count": 0,
            "idle_count": 2,
        }
    )
    assert len(session.added) == 1
    metric = session.added[0]
    assert metric.memory_current == 300_000_000
    assert metric.memory_max is None
    assert session.committed


async def test_sampler_persists_row_with_known_limit():
    session = await _run_with_heartbeat(
        {
            "worker_id": "w1",
            "memory_current_bytes": 300_000_000,
            "memory_max_bytes": 8_000_000_000,
            "pool_size": 1,
            "busy_count": 1,
            "idle_count": 0,
        }
    )
    assert len(session.added) == 1
    assert session.added[0].memory_max == 8_000_000_000


async def test_sampler_skips_when_current_missing():
    session = await _run_with_heartbeat(
        {
            "worker_id": "w1",
            "memory_current_bytes": -1,
            "memory_max_bytes": -1,
            "pool_size": 0,
            "busy_count": 0,
            "idle_count": 0,
        }
    )
    assert session.added == []
    assert not session.committed
