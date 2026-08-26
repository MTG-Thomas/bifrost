"""Atomic lifecycle operations for durable infrastructure attempts."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.execution_policy import ExecutionOperationsPolicy
from src.models.orm.execution_attempts import ExecutionAttempt


ACTIVE_ATTEMPT_STATUSES = ("claimed", "running", "waiting")
TERMINAL_ATTEMPT_STATUSES = (
    "succeeded",
    "failed",
    "cancelled",
    "worker_lost",
    "admission_deferred",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def start_execution_attempt(
    db: AsyncSession,
    *,
    logical_job_type: str,
    logical_job_id: UUID,
    organization_id: UUID | None,
    policy: ExecutionOperationsPolicy,
    status: str = "claimed",
    attempt_number: int | None = None,
    queue_name: str | None = None,
    message_id: str | None = None,
    retry_count: int = 0,
    replay_count: int = 0,
    worker_id: str | None = None,
    process_id: int | None = None,
    lease_token: UUID | None = None,
) -> ExecutionAttempt:
    """Allocate one monotonically numbered attempt under a transaction lock."""

    if status not in ACTIVE_ATTEMPT_STATUSES:
        raise ValueError(f"invalid initial execution-attempt status: {status}")
    await db.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtext('bifrost:execution-attempt:' || :logical_key))"
        ),
        {"logical_key": f"{logical_job_type}:{logical_job_id}"},
    )
    current_number = (
        await db.execute(
            select(func.max(ExecutionAttempt.attempt_number)).where(
                ExecutionAttempt.logical_job_type == logical_job_type,
                ExecutionAttempt.logical_job_id == logical_job_id,
            )
        )
    ).scalar_one_or_none()
    next_number = (current_number or 0) + 1
    # Existing durable jobs can predate this ledger and therefore legitimately
    # start with a number greater than one. Preserve their canonical counter
    # while still rejecting duplicates and regressions.
    if attempt_number is not None and attempt_number <= (current_number or 0):
        raise ValueError(
            f"attempt number {attempt_number} is not greater than "
            f"{current_number or 0} for {logical_job_type}:{logical_job_id}"
        )

    attempt = ExecutionAttempt(
        logical_job_type=logical_job_type,
        logical_job_id=logical_job_id,
        organization_id=organization_id,
        attempt_number=attempt_number or next_number,
        status=status,
        policy_identifier=policy.identifier,
        workload_class=policy.workload_class.value,
        mechanism=policy.mechanism.value,
        queue_name=queue_name,
        message_id=message_id,
        retry_count=max(0, retry_count),
        replay_count=max(0, replay_count),
        worker_id=worker_id,
        process_id=process_id,
        lease_token=lease_token,
    )
    db.add(attempt)
    await db.flush()
    return attempt


async def transition_execution_attempt(
    db: AsyncSession,
    *,
    status: str,
    attempt_id: UUID | None = None,
    logical_job_type: str | None = None,
    logical_job_id: UUID | None = None,
    lease_token: UUID | None = None,
    worker_id: str | None = None,
    process_id: int | None = None,
    failure_code: str | None = None,
    failure_message: str | None = None,
) -> ExecutionAttempt | None:
    """Fenced transition of one active attempt to running, waiting, or terminal."""

    allowed = {*ACTIVE_ATTEMPT_STATUSES, *TERMINAL_ATTEMPT_STATUSES}
    if status not in allowed:
        raise ValueError(f"invalid execution-attempt status: {status}")
    if attempt_id is None and (
        logical_job_type is None or logical_job_id is None
    ):
        raise ValueError("attempt_id or logical job identity is required")

    query = select(ExecutionAttempt).where(
        ExecutionAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES)
    )
    if attempt_id is not None:
        query = query.where(ExecutionAttempt.id == attempt_id)
    else:
        query = query.where(
            ExecutionAttempt.logical_job_type == logical_job_type,
            ExecutionAttempt.logical_job_id == logical_job_id,
        ).order_by(ExecutionAttempt.attempt_number.desc())
    if lease_token is not None:
        query = query.where(ExecutionAttempt.lease_token == lease_token)

    attempt = (
        await db.execute(query.limit(1).with_for_update())
    ).scalar_one_or_none()
    if attempt is None:
        return None
    attempt.status = status
    if worker_id is not None:
        attempt.worker_id = worker_id
    if process_id is not None:
        attempt.process_id = process_id
    attempt.failure_code = failure_code
    attempt.failure_message = (
        failure_message.strip()[:4000] if failure_message else None
    )
    if status in TERMINAL_ATTEMPT_STATUSES:
        attempt.completed_at = _now()
    await db.flush()
    return attempt


async def list_execution_attempts(
    db: AsyncSession,
    *,
    logical_job_type: str,
    logical_job_id: UUID,
) -> list[ExecutionAttempt]:
    return list(
        (
            await db.execute(
                select(ExecutionAttempt)
                .where(
                    ExecutionAttempt.logical_job_type == logical_job_type,
                    ExecutionAttempt.logical_job_id == logical_job_id,
                )
                .order_by(ExecutionAttempt.attempt_number.asc())
            )
        )
        .scalars()
        .all()
    )
