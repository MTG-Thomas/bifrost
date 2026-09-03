"""Unit contracts for token-fenced workflow execution attempts."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution, WorkflowExecutionAttempt as ExecutionAttempt

from src.services.execution.attempts import (
    create_claimed_attempt,
    ensure_dispatch_attempt,
    failure_attempt_status,
    finalize_attempt,
    heartbeat_attempt_tokens,
    mark_attempt_running,
    mark_attempt_published,
)


@pytest.mark.asyncio
async def test_heartbeat_accepts_claimed_and_running_dispatch_phases(monkeypatch) -> None:
    claimed_token = uuid4()
    running_token = uuid4()
    observed_statement = None

    class _Result:
        def all(self):
            return [(claimed_token,), (running_token,)]

    session = SimpleNamespace(commit=AsyncMock())

    async def execute(statement):
        nonlocal observed_statement
        observed_statement = statement
        return _Result()

    session.execute = execute

    @asynccontextmanager
    async def db_context():
        yield session

    monkeypatch.setattr("src.core.database.get_db_context", db_context)

    accepted = await heartbeat_attempt_tokens([claimed_token, running_token])

    assert accepted == {claimed_token, running_token}
    assert observed_statement is not None
    statuses = observed_statement.compile().params["status_1"]
    assert set(statuses) == {"claimed", "running"}
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_creates_next_attempt_without_copying_payloads() -> None:
    execution = SimpleNamespace(
        id=uuid4(),
        runtime_mode="deployment-v1",
        runtime_evidence_hash="sha256:runtime",
    )
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[None, 2]),
        add=MagicMock(),
        flush=AsyncMock(),
    )

    claim = await create_claimed_attempt(
        session,
        execution,
        worker_id="worker-one",
        worker_incarnation_id=uuid4(),
    )

    attempt = session.add.call_args.args[0]
    assert claim.attempt_number == 3
    assert attempt.claim_token == claim.claim_token
    assert attempt.runtime_evidence_hash == "sha256:runtime"
    assert not hasattr(attempt, "parameters")
    assert not hasattr(attempt, "result")
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_attempt_is_published_then_claimed_with_fresh_token() -> None:
    execution = SimpleNamespace(
        id=uuid4(),
        runtime_mode="deployment-v1",
        runtime_evidence_hash="sha256:runtime",
        dispatch_evidence_hash="sha256:dispatch",
        attempt_tracking_version=None,
    )
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=None),
        add=MagicMock(),
        flush=AsyncMock(),
    )
    attempt = await ensure_dispatch_attempt(session, execution)
    session.scalar.return_value = attempt

    published = await mark_attempt_published(session, execution)
    claim = await create_claimed_attempt(
        session,
        execution,
        worker_id="slot-a",
        worker_incarnation_id=uuid4(),
    )

    assert published is attempt
    assert attempt.published_at is not None
    assert attempt.status == "claimed"
    assert attempt.phase == "claim"
    assert attempt.claim_token == claim.claim_token
    assert execution.attempt_tracking_version == "v1"


@pytest.mark.asyncio
async def test_claim_rejects_a_second_active_attempt() -> None:
    execution = SimpleNamespace(id=uuid4())
    session = SimpleNamespace(
        scalar=AsyncMock(
            return_value=SimpleNamespace(status="running", claim_token=uuid4())
        )
    )

    with pytest.raises(ValueError, match="active attempt"):
        await create_claimed_attempt(
            session,
            execution,
            worker_id="worker-two",
            worker_incarnation_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_finalize_attempt_rejects_a_stale_token() -> None:
    session = SimpleNamespace(scalar=AsyncMock(return_value=None), flush=AsyncMock())

    accepted = await finalize_attempt(
        session, uuid4(), uuid4(), status="succeeded", phase="terminal"
    )

    assert accepted is False
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_attempt_records_bounded_terminal_evidence() -> None:
    attempt = SimpleNamespace(
        status="running",
        phase="execution",
        failure_code=None,
        duration_ms=None,
        peak_memory_bytes=None,
        cpu_total_seconds=None,
        heartbeat_at=None,
        completed_at=None,
    )
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=attempt), flush=AsyncMock()
    )

    accepted = await finalize_attempt(
        session,
        uuid4(),
        uuid4(),
        status="worker_lost",
        phase="terminal",
        failure_code="worker_process_lost",
        duration_ms=125,
        peak_memory_bytes=2048,
        cpu_total_seconds=0.5,
    )

    assert accepted is True
    assert attempt.status == "worker_lost"
    assert attempt.phase == "terminal"
    assert attempt.failure_code == "worker_process_lost"
    assert attempt.duration_ms == 125
    assert attempt.peak_memory_bytes == 2048
    assert attempt.cpu_total_seconds == 0.5
    assert isinstance(attempt.completed_at, datetime)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_attempt_running_is_monotonic() -> None:
    attempt = SimpleNamespace(
        status="claimed",
        phase="claim",
        started_at=None,
        heartbeat_at=None,
        process_id=None,
    )
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=attempt), flush=AsyncMock()
    )

    assert await mark_attempt_running(
        session, uuid4(), uuid4(), process_id="process-4"
    )
    assert attempt.status == "running"
    assert attempt.phase == "execution"
    assert attempt.process_id == "process-4"
    assert attempt.started_at <= datetime.now(timezone.utc)

    attempt.status = "failed"
    assert not await mark_attempt_running(session, uuid4(), uuid4())


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [
        ("TimeoutError", ("timed_out", "execution_timeout")),
        ("CancelledError", ("cancelled", "cancelled")),
        ("ProcessCrashError", ("worker_lost", "worker_process_lost")),
        ("OrphanedExecution", ("worker_lost", "worker_process_lost")),
        ("WorkerShutdownError", ("worker_lost", "worker_process_lost")),
        ("ResultPersistenceError", ("failed", "result_persist_failed")),
        ("ValueError", ("failed", "tenant_code_error")),
    ],
)
def test_failure_types_map_to_stable_attempt_codes(
    error_type: str, expected: tuple[str, str]
) -> None:
    assert failure_attempt_status(error_type) == expected


@pytest.mark.asyncio
async def test_token_cannot_finalize_an_attempt_for_another_execution(
    db_session,
) -> None:
    first = Execution(
        id=uuid4(),
        workflow_name="first",
        status=ExecutionStatus.RUNNING,
        parameters={},
        executed_by_name="test",
    )
    second = Execution(
        id=uuid4(),
        workflow_name="second",
        status=ExecutionStatus.RUNNING,
        parameters={},
        executed_by_name="test",
    )
    db_session.add_all([first, second])
    await db_session.flush()
    claim = await create_claimed_attempt(
        db_session,
        first,
        worker_id="slot-a",
        worker_incarnation_id=uuid4(),
    )

    assert not await finalize_attempt(
        db_session,
        second.id,
        claim.claim_token,
        status="succeeded",
        phase="terminal",
    )
    attempt = await db_session.scalar(
        select(ExecutionAttempt).where(ExecutionAttempt.id == claim.attempt_id)
    )
    assert attempt is not None
    assert attempt.completed_at is None
    assert attempt.status == "claimed"


@pytest.mark.asyncio
async def test_database_rejects_two_active_attempts(db_session) -> None:
    execution = Execution(
        id=uuid4(),
        workflow_name="unique-active",
        status=ExecutionStatus.RUNNING,
        parameters={},
        executed_by_name="test",
    )
    db_session.add(execution)
    await db_session.flush()
    await create_claimed_attempt(
        db_session,
        execution,
        worker_id="slot-a",
        worker_incarnation_id=uuid4(),
    )
    now = datetime.now(timezone.utc)
    db_session.add(
        ExecutionAttempt(
            id=uuid4(),
            execution_id=execution.id,
            attempt_number=2,
            claim_token=uuid4(),
            status="claimed",
            phase="claim",
            published_at=now,
            claimed_at=now,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_database_rejects_unfenceable_claimed_attempt(db_session) -> None:
    execution = Execution(
        id=uuid4(),
        workflow_name="missing-claim-shape",
        status=ExecutionStatus.RUNNING,
        parameters={},
        executed_by_name="test",
    )
    db_session.add(execution)
    await db_session.flush()
    db_session.add(
        ExecutionAttempt(
            id=uuid4(),
            execution_id=execution.id,
            attempt_number=1,
            status="claimed",
            phase="claim",
            # A claimed row without a token/timestamps could never fence a
            # heartbeat or completion and must be rejected by PostgreSQL.
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_terminal_attempt_allows_next_ordinal(db_session) -> None:
    execution = Execution(
        id=uuid4(),
        workflow_name="next-ordinal",
        status=ExecutionStatus.RUNNING,
        parameters={},
        executed_by_name="test",
    )
    db_session.add(execution)
    await db_session.flush()
    first = await create_claimed_attempt(
        db_session,
        execution,
        worker_id="slot-a",
        worker_incarnation_id=uuid4(),
    )
    assert await finalize_attempt(
        db_session,
        execution.id,
        first.claim_token,
        status="admission_rejected",
        phase="terminal",
        failure_code="admission_rejected",
    )
    second = await create_claimed_attempt(
        db_session,
        execution,
        worker_id="slot-b",
        worker_incarnation_id=uuid4(),
    )

    assert second.attempt_number == 2


@pytest.mark.asyncio
async def test_database_cascades_attempts_with_execution(db_session) -> None:
    execution = Execution(
        id=uuid4(),
        workflow_name="cascade",
        status=ExecutionStatus.RUNNING,
        parameters={},
        executed_by_name="test",
    )
    db_session.add(execution)
    await db_session.flush()
    claim = await create_claimed_attempt(
        db_session,
        execution,
        worker_id="slot-a",
        worker_incarnation_id=uuid4(),
    )
    execution_id = execution.id
    attempt_id = claim.attempt_id
    await db_session.commit()

    await db_session.execute(delete(Execution).where(Execution.id == execution_id))
    await db_session.commit()
    remaining = await db_session.scalar(
        select(func.count()).select_from(ExecutionAttempt).where(
            ExecutionAttempt.id == attempt_id
        )
    )
    assert remaining == 0


@pytest.mark.asyncio
async def test_database_rejects_invalid_attempt_state(db_session) -> None:
    execution = Execution(
        id=uuid4(),
        workflow_name="invalid-state",
        status=ExecutionStatus.RUNNING,
        parameters={},
        executed_by_name="test",
    )
    db_session.add(execution)
    await db_session.flush()
    db_session.add(
        ExecutionAttempt(
            id=uuid4(),
            execution_id=execution.id,
            attempt_number=1,
            claim_token=uuid4(),
            status="invented",
            phase="claim",
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_cancelled_attempt_rejects_late_success(db_session) -> None:
    execution = Execution(
        id=uuid4(),
        workflow_name="cancel-race",
        status=ExecutionStatus.CANCELLING,
        parameters={},
        executed_by_name="test",
    )
    db_session.add(execution)
    await db_session.flush()
    claim = await create_claimed_attempt(
        db_session,
        execution,
        worker_id="slot-a",
        worker_incarnation_id=uuid4(),
    )
    execution_id = execution.id
    assert await finalize_attempt(
        db_session,
        execution_id,
        claim.claim_token,
        status="cancelled",
        phase="terminal",
        failure_phase="cancellation",
        failure_code="cancelled",
    )
    assert not await finalize_attempt(
        db_session,
        execution.id,
        claim.claim_token,
        status="succeeded",
        phase="terminal",
    )


@pytest.mark.asyncio
async def test_parent_and_attempt_terminalization_roll_back_together(
    db_session,
) -> None:
    execution = Execution(
        id=uuid4(),
        workflow_name="atomic-rollback",
        status=ExecutionStatus.RUNNING,
        parameters={},
        executed_by_name="test",
    )
    db_session.add(execution)
    await db_session.flush()
    claim = await create_claimed_attempt(
        db_session,
        execution,
        worker_id="slot-a",
        worker_incarnation_id=uuid4(),
    )
    execution_id = execution.id
    attempt_id = claim.attempt_id
    await db_session.commit()

    assert await finalize_attempt(
        db_session,
        execution_id,
        claim.claim_token,
        status="failed",
        phase="terminal",
        failure_phase="execution",
        failure_code="tenant_code_error",
    )
    execution.status = ExecutionStatus.FAILED
    await db_session.flush()
    await db_session.rollback()

    refreshed_execution = await db_session.get(Execution, execution_id)
    refreshed_attempt = await db_session.get(ExecutionAttempt, attempt_id)
    assert refreshed_execution is not None
    assert refreshed_execution.status == ExecutionStatus.RUNNING
    assert refreshed_attempt is not None
    assert refreshed_attempt.completed_at is None
