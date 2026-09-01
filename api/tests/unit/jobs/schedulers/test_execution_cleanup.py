from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.jobs.schedulers import execution_cleanup
from src.jobs.schedulers.execution_cleanup import (
    _execution_age_anchor,
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
        "live_worker_incarnation_ids": set(),
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
        "live_worker_incarnation_ids": set(),
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
        "live_worker_incarnation_ids": set(),
        "oldest_worker_started_at": now - timedelta(seconds=30),
        "heartbeat_count": 3,
    }

    assert not _is_restart_orphan(execution, now=now, heartbeat_state=heartbeat_state)


def test_is_restart_orphan_when_attempt_owner_disappears_among_older_live_workers():
    now = datetime.now(timezone.utc)
    execution = SimpleNamespace(
        id=uuid4(),
        started_at=now - timedelta(minutes=15),
    )
    active_attempt = SimpleNamespace(
        worker_incarnation_id=uuid4(),
        heartbeat_at=now - timedelta(minutes=3),
    )
    heartbeat_state = {
        "active_execution_ids": set(),
        "live_worker_incarnation_ids": {str(uuid4()), str(uuid4())},
        "oldest_worker_started_at": now - timedelta(hours=1),
        "heartbeat_count": 2,
    }

    assert _is_restart_orphan(
        execution,
        now=now,
        heartbeat_state=heartbeat_state,
        active_attempt=active_attempt,
    )


def test_is_restart_orphan_keeps_attempt_with_live_owner_incarnation():
    now = datetime.now(timezone.utc)
    owner_incarnation_id = uuid4()
    execution = SimpleNamespace(
        id=uuid4(),
        started_at=now - timedelta(minutes=15),
    )
    active_attempt = SimpleNamespace(
        worker_incarnation_id=owner_incarnation_id,
        heartbeat_at=now - timedelta(minutes=3),
    )
    heartbeat_state = {
        "active_execution_ids": set(),
        "live_worker_incarnation_ids": {str(owner_incarnation_id), str(uuid4())},
        "oldest_worker_started_at": now - timedelta(hours=1),
        "heartbeat_count": 2,
    }

    assert not _is_restart_orphan(
        execution,
        now=now,
        heartbeat_state=heartbeat_state,
        active_attempt=active_attempt,
    )


def test_runner_loss_attempt_limit_is_fail_closed(monkeypatch):
    monkeypatch.delenv("BIFROST_WORKFLOW_RUNNER_LOSS_MAX_ATTEMPTS", raising=False)
    assert execution_cleanup._runner_loss_max_attempts() == 1

    monkeypatch.setenv("BIFROST_WORKFLOW_RUNNER_LOSS_MAX_ATTEMPTS", "2")
    assert execution_cleanup._runner_loss_max_attempts() == 2

    monkeypatch.setenv("BIFROST_WORKFLOW_RUNNER_LOSS_MAX_ATTEMPTS", "invalid")
    assert execution_cleanup._runner_loss_max_attempts() == 1


def test_restart_orphan_grace_is_overridable_for_chaos_stacks(monkeypatch):
    monkeypatch.delenv(
        "BIFROST_WORKFLOW_RESTART_ORPHAN_GRACE_SECONDS", raising=False
    )
    assert execution_cleanup._restart_orphan_grace_seconds() == 120

    monkeypatch.setenv("BIFROST_WORKFLOW_RESTART_ORPHAN_GRACE_SECONDS", "1")
    assert execution_cleanup._restart_orphan_grace_seconds() == 1


@pytest.mark.asyncio
async def test_recover_restart_orphan_fences_attempt_and_republishes(monkeypatch):
    execution_id = uuid4()
    claim_token = uuid4()
    execution = SimpleNamespace(
        id=execution_id,
        status="Running",
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        duration_ms=100,
        error_message="old",
    )
    attempt = SimpleNamespace(claim_token=claim_token)
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[1, attempt]))
    finalize = AsyncMock(return_value=True)
    republish = AsyncMock()
    transition = AsyncMock()
    monkeypatch.setenv("BIFROST_WORKFLOW_RUNNER_LOSS_MAX_ATTEMPTS", "2")

    with (
        patch(
            "src.services.execution.attempts.finalize_attempt",
            finalize,
        ),
        patch(
            "src.services.execution.async_executor.republish_execution_from_dispatch",
            republish,
        ),
        patch.object(execution_cleanup, "transition_execution_attempt", transition),
    ):
        recovered = await execution_cleanup._recover_restart_orphan(db, execution)

    assert recovered is True
    finalize.assert_awaited_once_with(
        db,
        execution_id,
        claim_token,
        status="worker_lost",
        phase="terminal",
        failure_code="worker_lost",
        failure_phase="worker",
    )
    transition.assert_awaited_once()
    republish.assert_awaited_once_with(execution)
    assert execution.status == "Pending"
    assert execution.started_at is None
    assert execution.error_message is None


@pytest.mark.asyncio
async def test_recover_restart_orphan_stops_at_attempt_limit(monkeypatch):
    execution = SimpleNamespace(id=uuid4())
    db = SimpleNamespace(scalar=AsyncMock(return_value=2))
    monkeypatch.setenv("BIFROST_WORKFLOW_RUNNER_LOSS_MAX_ATTEMPTS", "2")

    assert not await execution_cleanup._recover_restart_orphan(db, execution)


@pytest.mark.asyncio
async def test_recover_restart_orphan_publish_failure_preserves_state(monkeypatch):
    execution_id = uuid4()
    claim_token = uuid4()
    started_at = datetime.now(timezone.utc)
    execution = SimpleNamespace(
        id=execution_id,
        status="Running",
        started_at=started_at,
        completed_at=None,
        duration_ms=100,
        error_message=None,
    )
    attempt = SimpleNamespace(claim_token=claim_token)
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[1, attempt]))
    finalize = AsyncMock(return_value=True)
    republish = AsyncMock(side_effect=ConnectionError("broker unavailable"))
    transition = AsyncMock()
    monkeypatch.setenv("BIFROST_WORKFLOW_RUNNER_LOSS_MAX_ATTEMPTS", "2")

    with (
        patch("src.services.execution.attempts.finalize_attempt", finalize),
        patch(
            "src.services.execution.async_executor.republish_execution_from_dispatch",
            republish,
        ),
        patch.object(execution_cleanup, "transition_execution_attempt", transition),
        pytest.raises(ConnectionError, match="broker unavailable"),
    ):
        await execution_cleanup._recover_restart_orphan(db, execution)

    finalize.assert_not_awaited()
    transition.assert_not_awaited()
    assert execution.status == "Running"
    assert execution.started_at == started_at


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
        "live_worker_incarnation_ids": set(),
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


@pytest.mark.asyncio
async def test_load_worker_heartbeat_failure_does_not_block_age_cleanup(monkeypatch):
    redis = SimpleNamespace(
        scan=AsyncMock(side_effect=ConnectionError("Redis DNS unavailable"))
    )
    monkeypatch.setattr(execution_cleanup, "get_redis_client", lambda: redis)

    state = await _load_worker_heartbeat_state(datetime.now(timezone.utc))

    assert state == {
        "active_execution_ids": set(),
        "live_worker_incarnation_ids": set(),
        "oldest_worker_started_at": None,
        "heartbeat_count": 0,
    }


def test_execution_age_anchor_uses_scheduled_then_created_for_null_start() -> None:
    created_at = datetime(2026, 8, 17, tzinfo=timezone.utc)
    scheduled_at = datetime(2026, 8, 18, tzinfo=timezone.utc)

    assert _execution_age_anchor(
        SimpleNamespace(
            started_at=None,
            scheduled_at=scheduled_at,
            created_at=created_at,
        )
    ) == scheduled_at
    assert _execution_age_anchor(
        SimpleNamespace(
            started_at=None,
            scheduled_at=None,
            created_at=created_at,
        )
    ) == created_at


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _QueryResult:
    def __init__(self, rows, *, tuple_rows=False):
        self._rows = rows
        self._tuple_rows = tuple_rows

    def scalars(self):
        assert not self._tuple_rows
        return _ScalarRows(self._rows)

    def all(self):
        assert self._tuple_rows
        return self._rows

    def scalar_one_or_none(self):
        assert not self._tuple_rows
        assert len(self._rows) <= 1
        return self._rows[0] if self._rows else None


class _CleanupSession:
    def __init__(self, results):
        self._results = list(results)
        self._executions = {}
        for result in self._results:
            for row in result._rows:
                execution = row[0] if result._tuple_rows else row
                if hasattr(execution, "id"):
                    self._executions[execution.id] = execution
        self._execution_rereads = list(self._executions.values())
        self.execute = AsyncMock(side_effect=self._execute)
        self.scalar = AsyncMock(side_effect=self._scalar)
        self.commit = AsyncMock()
        self.add = MagicMock()

    async def _execute(self, query, _params=None):
        if "pg_advisory_xact_lock" in str(query):
            return _QueryResult([])
        return self._results.pop(0)

    async def _scalar(self, query):
        statement = str(query)
        if "FROM executions" in statement:
            if not self._execution_rereads:
                raise AssertionError("unexpected execution row re-read")
            return self._execution_rereads.pop(0)
        if "FROM workflows" in statement:
            return 1800
        if "FROM workflow_execution_attempts" in statement:
            return None
        raise AssertionError(f"unexpected scalar query: {statement}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.asyncio
async def test_cleanup_sweeps_overdue_scheduled_and_null_start_running_rows() -> None:
    from src.models.enums import ExecutionStatus

    now = datetime.now(timezone.utc)

    def row(status, age_minutes):
        return SimpleNamespace(
            id=uuid4(),
            status=status.value,
            started_at=None,
            scheduled_at=now - timedelta(minutes=age_minutes),
            created_at=now - timedelta(minutes=age_minutes + 1),
            workflow_name=f"{status.value} workflow",
            workflow_id=None,
            executed_by=uuid4(),
            executed_by_name="Scheduler",
            organization_id=None,
        )

    scheduled = row(ExecutionStatus.SCHEDULED, 2 * 24 * 60)
    running = row(ExecutionStatus.RUNNING, 45)
    running.scheduled_at = None
    session = _CleanupSession(
        [
            _QueryResult([scheduled]),
            _QueryResult([(running, 1800, None)], tuple_rows=True),
            _QueryResult([]),
            _QueryResult([]),
        ]
    )
    redis = SimpleNamespace(
        scan=AsyncMock(side_effect=ConnectionError("Redis DNS unavailable")),
        delete_pending_execution=AsyncMock(
            side_effect=ConnectionError("Redis DNS unavailable")
        ),
    )

    with (
        patch.object(execution_cleanup, "get_session_factory", return_value=lambda: session),
        patch.object(execution_cleanup, "get_redis_client", return_value=redis),
        patch(
            "src.services.execution.queue_tracker.remove_from_queue",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Redis DNS unavailable"),
        ),
        patch.object(
            execution_cleanup,
            "publish_execution_update",
            new_callable=AsyncMock,
        ),
        patch.object(
            execution_cleanup,
            "publish_history_update",
            new_callable=AsyncMock,
        ),
        patch.object(
            execution_cleanup,
            "_cleanup_stale_agent_runs",
            new_callable=AsyncMock,
            return_value={
                "agent_run_queued_timeouts": 0,
                "agent_run_running_timeouts": 0,
                "agent_run_total_cleaned": 0,
                "agent_run_errors": [],
            },
        ),
        patch.object(
            execution_cleanup,
            "transition_execution_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await execution_cleanup.cleanup_stuck_executions()

    assert result["scheduled_timeouts"] == 1
    assert result["running_timeouts"] == 1
    assert result["total_cleaned"] == 2
    assert scheduled.status == ExecutionStatus.FAILED.value
    assert running.status == ExecutionStatus.TIMEOUT.value
    assert session.commit.await_count == 1
    assert session.add.call_count == 2

    scheduled_query = str(session.execute.await_args_list[0].args[0])
    assert "coalesce(executions.scheduled_at, executions.created_at)" in scheduled_query


@pytest.mark.asyncio
async def test_cleanup_does_not_terminalize_old_pending_rows() -> None:
    session = _CleanupSession(
        [
            _QueryResult([]),
            _QueryResult([], tuple_rows=True),
            _QueryResult([]),
        ]
    )
    with (
        patch.object(execution_cleanup, "get_session_factory", return_value=lambda: session),
        patch.object(
            execution_cleanup,
            "_load_worker_heartbeat_state",
            new_callable=AsyncMock,
            return_value={
                "active_execution_ids": set(),
                "oldest_worker_started_at": None,
                "heartbeat_count": 0,
            },
        ),
        patch.object(
            execution_cleanup,
            "_cleanup_stale_agent_runs",
            new_callable=AsyncMock,
            return_value={
                "agent_run_queued_timeouts": 0,
                "agent_run_running_timeouts": 0,
                "agent_run_total_cleaned": 0,
                "agent_run_errors": [],
            },
        ),
    ):
        result = await execution_cleanup.cleanup_stuck_executions()

    assert result["pending_timeouts"] == 0
    assert session.execute.await_count == 3
