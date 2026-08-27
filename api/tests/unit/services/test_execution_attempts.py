"""Focused contracts for the shared durable execution-attempt ledger."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.jobs.execution_policy import broker_execution_policies
from src.services.execution_attempts import (
    REQUIRED_EXECUTION_OPERATIONS_TABLES,
    require_execution_operations_schema,
    start_execution_attempt,
    transition_execution_attempt,
)


@pytest.mark.asyncio
async def test_worker_schema_check_fails_before_consuming_missing_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = [
        (name, name if name != "execution_attempts" else None)
        for name in REQUIRED_EXECUTION_OPERATIONS_TABLES
    ]
    db.execute.return_value = result

    @asynccontextmanager
    async def db_context():
        yield db

    monkeypatch.setattr("src.core.database.get_db_context", db_context)

    with pytest.raises(RuntimeError, match="missing tables: execution_attempts"):
        await require_execution_operations_schema()


@pytest.mark.asyncio
async def test_start_attempt_allocates_next_number_and_policy_snapshot() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    current = MagicMock()
    current.scalar_one_or_none.return_value = 2
    event_sequence = MagicMock()
    event_sequence.scalar_one_or_none.return_value = None
    db.execute.side_effect = [MagicMock(), current, event_sequence]

    logical_id = uuid4()
    policy = broker_execution_policies()["workflow-executions"]
    attempt = await start_execution_attempt(
        db,
        logical_job_type="workflow_execution",
        logical_job_id=logical_id,
        organization_id=None,
        policy=policy,
        status="running",
        queue_name="workflow-executions",
    )

    assert attempt.attempt_number == 3
    assert attempt.policy_identifier == policy.identifier
    assert attempt.workload_class == policy.workload_class.value
    assert attempt.admission_policy == policy.admission_policy.value
    assert attempt.mechanism == policy.mechanism.value
    assert db.add.call_count == 2
    assert db.flush.await_count == 2


@pytest.mark.asyncio
async def test_start_attempt_allows_legacy_counter_gap_but_not_regression() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    current = MagicMock()
    current.scalar_one_or_none.return_value = 3
    event_sequence = MagicMock()
    event_sequence.scalar_one_or_none.return_value = None
    db.execute.side_effect = [MagicMock(), current, event_sequence]

    attempt = await start_execution_attempt(
        db,
        logical_job_type="platform_job",
        logical_job_id=uuid4(),
        organization_id=None,
        policy=broker_execution_policies()["workflow-executions"],
        attempt_number=8,
    )
    assert attempt.attempt_number == 8

    db = AsyncMock()
    current = MagicMock()
    current.scalar_one_or_none.return_value = 8
    db.execute.side_effect = [MagicMock(), current]
    with pytest.raises(ValueError, match="not greater than"):
        await start_execution_attempt(
            db,
            logical_job_type="platform_job",
            logical_job_id=uuid4(),
            organization_id=None,
            policy=broker_execution_policies()["workflow-executions"],
            attempt_number=8,
        )


@pytest.mark.asyncio
async def test_terminal_transition_records_bounded_failure() -> None:
    attempt = SimpleNamespace(
        id=uuid4(),
        logical_job_type="workflow_execution",
        logical_job_id=uuid4(),
        organization_id=None,
        policy_identifier="broker.workflow-executions",
        workload_class="workflow_execution",
        admission_policy="process_slot_and_memory",
        mechanism="rabbitmq",
        attempt_number=1,
        started_at=datetime.now(timezone.utc),
        status="running",
        worker_id=None,
        process_id=None,
        failure_code=None,
        failure_message=None,
        completed_at=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = attempt
    event_sequence = MagicMock()
    event_sequence.scalar_one_or_none.return_value = 1
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.side_effect = [result, event_sequence]

    changed = await transition_execution_attempt(
        db,
        attempt_id=uuid4(),
        status="failed",
        failure_code="boom",
        failure_message="x" * 5000,
    )

    assert changed is attempt
    assert attempt.status == "failed"
    assert attempt.failure_code == "boom"
    assert len(attempt.failure_message) == 4000
    assert attempt.completed_at is not None
    assert db.flush.await_count == 2


@pytest.mark.asyncio
async def test_transition_is_fenced_to_active_attempt() -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute.return_value = result

    assert (
        await transition_execution_attempt(
            db,
            logical_job_type="platform_job",
            logical_job_id=uuid4(),
            lease_token=uuid4(),
            status="worker_lost",
        )
        is None
    )
    db.flush.assert_not_awaited()
