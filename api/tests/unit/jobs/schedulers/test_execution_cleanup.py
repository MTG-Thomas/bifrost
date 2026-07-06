from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.jobs.schedulers import execution_cleanup
from src.jobs.schedulers.execution_cleanup import (
    _is_restart_orphan,
    _load_worker_heartbeat_state,
    _parse_heartbeat_time,
)


class _FakeRedis:
    def __init__(self, pages, values):
        self._pages = list(pages)
        self._values = values

    async def scan(self, cursor, match=None, count=100):
        assert match == "bifrost:pool:*:heartbeat"
        assert count == 100
        assert cursor in (0, 1)
        return self._pages.pop(0)

    async def get(self, key):
        return self._values.get(key)


def test_is_restart_orphan_when_execution_predates_current_workers():
    now = datetime.now(timezone.utc)
    execution = SimpleNamespace(
        id=uuid4(),
        started_at=now - timedelta(minutes=15),
    )
    heartbeat_state = {
        "active_execution_ids": set(),
        "oldest_worker_started_at": now - timedelta(minutes=5),
        "heartbeat_count": 3,
    }

    assert _is_restart_orphan(execution, now=now, heartbeat_state=heartbeat_state)


def test_is_restart_orphan_keeps_execution_claimed_by_heartbeat():
    now = datetime.now(timezone.utc)
    execution_id = uuid4()
    execution = SimpleNamespace(
        id=execution_id,
        started_at=now - timedelta(minutes=15),
    )
    heartbeat_state = {
        "active_execution_ids": {str(execution_id)},
        "oldest_worker_started_at": now - timedelta(minutes=5),
        "heartbeat_count": 3,
    }

    assert not _is_restart_orphan(execution, now=now, heartbeat_state=heartbeat_state)


def test_is_restart_orphan_waits_for_worker_grace_period():
    now = datetime.now(timezone.utc)
    execution = SimpleNamespace(
        id=uuid4(),
        started_at=now - timedelta(minutes=15),
    )
    heartbeat_state = {
        "active_execution_ids": set(),
        "oldest_worker_started_at": now - timedelta(seconds=30),
        "heartbeat_count": 3,
    }

    assert not _is_restart_orphan(execution, now=now, heartbeat_state=heartbeat_state)


def test_parse_heartbeat_time_normalizes_zulu_and_naive_values():
    assert _parse_heartbeat_time("2026-07-05T10:30:00Z") == datetime(
        2026, 7, 5, 10, 30, tzinfo=timezone.utc
    )
    assert _parse_heartbeat_time("2026-07-05T10:30:00") == datetime(
        2026, 7, 5, 10, 30, tzinfo=timezone.utc
    )


def test_parse_heartbeat_time_ignores_empty_or_invalid_values():
    assert _parse_heartbeat_time(None) is None
    assert _parse_heartbeat_time("not-a-date") is None


@pytest.mark.asyncio
async def test_load_worker_heartbeat_state_collects_active_executions_and_oldest_start(
    monkeypatch,
):
    redis = _FakeRedis(
        pages=[
            (1, ["heartbeat:one", "heartbeat:bad-json"]),
            (0, ["heartbeat:two", "heartbeat:empty"]),
        ],
        values={
            "heartbeat:one": (
                '{"started_at":"2026-07-05T10:00:00Z",'
                '"processes":[{"execution":{"execution_id":"exec-1"}},{}]}'
            ),
            "heartbeat:bad-json": "{",
            "heartbeat:two": (
                '{"started_at":"2026-07-05T09:00:00Z",'
                '"processes":[{"execution":{"execution_id":"exec-2"}}]}'
            ),
            "heartbeat:empty": None,
        },
    )
    monkeypatch.setattr(execution_cleanup, "get_redis_client", lambda: redis)

    state = await _load_worker_heartbeat_state(datetime(2026, 7, 5, tzinfo=timezone.utc))

    assert state["active_execution_ids"] == {"exec-1", "exec-2"}
    assert state["oldest_worker_started_at"] == datetime(
        2026, 7, 5, 9, 0, tzinfo=timezone.utc
    )
    assert state["heartbeat_count"] == 2


@pytest.mark.asyncio
async def test_load_worker_heartbeat_state_returns_empty_state_without_redis(monkeypatch):
    monkeypatch.setattr(execution_cleanup, "get_redis_client", lambda: None)

    state = await _load_worker_heartbeat_state(datetime.now(timezone.utc))

    assert state == {
        "active_execution_ids": set(),
        "oldest_worker_started_at": None,
        "heartbeat_count": 0,
    }
