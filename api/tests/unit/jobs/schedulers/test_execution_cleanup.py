from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.jobs.schedulers import execution_cleanup
from src.jobs.schedulers.execution_cleanup import (
    _is_restart_orphan,
    _load_worker_heartbeat_state,
    _parse_heartbeat_time,
)
from src.models.orm.agent_runs import AgentRun

cleanup = execution_cleanup


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


def _stale_time(minutes: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


def _fresh_time(minutes: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


async def _seed_agent_runs(db_session, agent, *, stale_minutes: int = 10) -> tuple[AgentRun, AgentRun, AgentRun]:
    agent.max_run_timeout = 60
    agent.updated_at = datetime.now(timezone.utc)

    queued = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        trigger_type="api",
        status="queued",
        iterations_used=0,
        tokens_used=0,
        created_at=_stale_time(stale_minutes),
    )
    running = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        trigger_type="api",
        status="running",
        iterations_used=1,
        tokens_used=25,
        created_at=_stale_time(stale_minutes),
        started_at=_stale_time(stale_minutes),
    )
    fresh_running = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        trigger_type="api",
        status="running",
        iterations_used=1,
        tokens_used=25,
        created_at=_fresh_time(2),
        started_at=_fresh_time(2),
    )

    db_session.add_all([agent, queued, running, fresh_running])
    await db_session.commit()
    return queued, running, fresh_running


def _patch_cleanup_dependencies(monkeypatch, async_session_factory):
    monkeypatch.setattr(cleanup, "get_session_factory", lambda: async_session_factory)
    monkeypatch.setattr(cleanup, "publish_execution_update", AsyncMock())
    monkeypatch.setattr(cleanup, "publish_history_update", AsyncMock())
    monkeypatch.setattr(cleanup, "publish_agent_run_update", AsyncMock())


async def _load_run(async_session_factory, run_id):
    async with async_session_factory() as db_session:
        result = await db_session.execute(select(AgentRun).where(AgentRun.id == run_id))
        return result.scalar_one()


def _agent_run_updates(mock_publish):
    return [(call.args[0].id, call.args[0].status) for call in mock_publish.await_args_list]


@pytest.mark.asyncio
class TestExecutionCleanupAgentRuns:
    async def test_cleanup_stale_agent_runs_terminalizes_and_broadcasts(
        self,
        db_session,
        async_session_factory,
        seed_agent,
        monkeypatch,
    ) -> None:
        _patch_cleanup_dependencies(monkeypatch, async_session_factory)
        queued, running, fresh_running = await _seed_agent_runs(db_session, seed_agent)

        results = await cleanup.cleanup_stuck_executions()

        assert results["agent_run_queued_timeouts"] == 1
        assert results["agent_run_running_timeouts"] == 1
        assert results["agent_run_total_cleaned"] == 2

        queued_reloaded = await _load_run(async_session_factory, queued.id)
        running_reloaded = await _load_run(async_session_factory, running.id)
        fresh_reloaded = await _load_run(async_session_factory, fresh_running.id)

        assert queued_reloaded.status == "failed"
        assert queued_reloaded.completed_at is not None
        assert "waiting in queue" in queued_reloaded.error

        assert running_reloaded.status == "timeout"
        assert running_reloaded.completed_at is not None
        assert "timed out after 360 seconds" in running_reloaded.error

        assert fresh_reloaded.status == "running"
        assert fresh_reloaded.completed_at is None

        assert cleanup.publish_agent_run_update.await_count == 2
        assert set(_agent_run_updates(cleanup.publish_agent_run_update)) == {
            (queued.id, "failed"),
            (running.id, "timeout"),
        }

    async def test_cleanup_is_idempotent_and_skips_active_runs(
        self,
        db_session,
        async_session_factory,
        seed_agent,
        monkeypatch,
    ) -> None:
        _patch_cleanup_dependencies(monkeypatch, async_session_factory)
        queued, running, fresh_running = await _seed_agent_runs(db_session, seed_agent)

        first = await cleanup.cleanup_stuck_executions()
        queued_first = await _load_run(async_session_factory, queued.id)
        running_first = await _load_run(async_session_factory, running.id)
        fresh_first = await _load_run(async_session_factory, fresh_running.id)

        second = await cleanup.cleanup_stuck_executions()
        queued_second = await _load_run(async_session_factory, queued.id)
        running_second = await _load_run(async_session_factory, running.id)
        fresh_second = await _load_run(async_session_factory, fresh_running.id)

        assert first["agent_run_total_cleaned"] == 2
        assert second["agent_run_total_cleaned"] == 0
        assert queued_first.status == queued_second.status == "failed"
        assert running_first.status == running_second.status == "timeout"
        assert fresh_first.status == fresh_second.status == "running"
        assert fresh_second.completed_at is None
