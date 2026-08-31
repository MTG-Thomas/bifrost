"""Durable, token-fenced lifecycle evidence for workflow execution attempts."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.executions import Execution, ExecutionAttempt


def _policy_digest(execution: Execution) -> str:
    evidence = {
        "attempt_policy": "workflow-attempt/v1",
        "runtime_mode": execution.runtime_mode,
    }
    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()

TERMINAL_ATTEMPT_STATUSES = frozenset(
    {
        "succeeded",
        "failed",
        "timed_out",
        "cancelled",
        "worker_lost",
        "admission_rejected",
    }
)
ATTEMPT_PHASES = frozenset(
    {"dispatch", "queue", "claim", "admission", "execution", "result", "terminal"}
)
FAILURE_PHASES = frozenset(
    {
        "dispatch",
        "queue",
        "claim",
        "admission",
        "execution",
        "result",
        "worker",
        "cancellation",
    }
)


@dataclass(frozen=True)
class AttemptClaim:
    attempt_id: UUID
    attempt_number: int
    claim_token: UUID


async def ensure_dispatch_attempt(
    session: AsyncSession, execution: Execution
) -> ExecutionAttempt:
    """Create attempt 1 with the durable execution pin, idempotently."""
    attempt = await session.scalar(
        select(ExecutionAttempt)
        .where(
            ExecutionAttempt.execution_id == execution.id,
            ExecutionAttempt.completed_at.is_(None),
        )
        .with_for_update()
    )
    if attempt is not None:
        return attempt
    attempt = ExecutionAttempt(
        id=uuid4(),
        execution_id=execution.id,
        attempt_number=1,
        claim_token=None,
        status="dispatching",
        phase="dispatch",
        runtime_mode=execution.runtime_mode,
        runtime_evidence_hash=execution.runtime_evidence_hash,
        dispatch_evidence_hash=execution.dispatch_evidence_hash,
        policy_digest=_policy_digest(execution),
    )
    execution.attempt_tracking_version = "v1"
    session.add(attempt)
    await session.flush()
    return attempt


async def mark_attempt_published(
    session: AsyncSession, execution: Execution
) -> ExecutionAttempt:
    """Record broker-confirmed publication while holding the execution fence."""
    attempt = await ensure_dispatch_attempt(session, execution)
    if attempt.status not in {"dispatching", "published"}:
        raise ValueError(f"attempt cannot be published from {attempt.status}")
    now = datetime.now(timezone.utc)
    attempt.status = "published"
    attempt.phase = "queue"
    attempt.published_at = attempt.published_at or now
    await session.flush()
    return attempt


async def has_recorded_attempt(session: AsyncSession, execution_id: UUID) -> bool:
    """Return whether this execution is ever allowed an unfenced result."""
    attempt_id = await session.scalar(
        select(ExecutionAttempt.id).where(
            ExecutionAttempt.execution_id == execution_id,
        )
    )
    return attempt_id is not None


async def create_claimed_attempt(
    session: AsyncSession,
    execution: Execution,
    *,
    worker_id: str | None,
    worker_incarnation_id: UUID | None,
) -> AttemptClaim:
    """Create the sole active attempt while the caller holds execution fencing."""

    active = await session.scalar(
        select(ExecutionAttempt)
        .where(
            ExecutionAttempt.execution_id == execution.id,
            ExecutionAttempt.completed_at.is_(None),
        )
        .with_for_update()
    )
    if active is not None:
        if active.status != "published" or active.claim_token is not None:
            raise ValueError("execution already has an active attempt")
        token = uuid4()
        active.claim_token = token
        active.status = "claimed"
        active.phase = "claim"
        active.worker_id = worker_id
        active.worker_incarnation_id = worker_incarnation_id
        active.claimed_at = datetime.now(timezone.utc)
        await session.flush()
        return AttemptClaim(active.id, active.attempt_number, token)

    last_number = await session.scalar(
        select(func.max(ExecutionAttempt.attempt_number)).where(
            ExecutionAttempt.execution_id == execution.id
        )
    )
    token = uuid4()
    now = datetime.now(timezone.utc)
    attempt = ExecutionAttempt(
        id=uuid4(),
        execution_id=execution.id,
        attempt_number=int(last_number or 0) + 1,
        claim_token=token,
        status="claimed",
        phase="claim",
        worker_id=worker_id,
        worker_incarnation_id=worker_incarnation_id,
        published_at=now,
        claimed_at=now,
        runtime_mode=execution.runtime_mode,
        runtime_evidence_hash=execution.runtime_evidence_hash,
        dispatch_evidence_hash=getattr(execution, "dispatch_evidence_hash", None),
        policy_digest=_policy_digest(execution),
    )
    session.add(attempt)
    await session.flush()
    return AttemptClaim(attempt.id, attempt.attempt_number, token)


async def mark_attempt_running(
    session: AsyncSession,
    execution_id: UUID,
    claim_token: UUID,
    *,
    process_id: str | None = None,
) -> bool:
    """Advance the currently claimed attempt; stale tokens are rejected."""

    attempt = await session.scalar(
        select(ExecutionAttempt)
        .where(
            ExecutionAttempt.claim_token == claim_token,
            ExecutionAttempt.execution_id == execution_id,
            ExecutionAttempt.completed_at.is_(None),
        )
        .with_for_update()
    )
    if attempt is None or attempt.status not in {"claimed", "running"}:
        return False
    now = datetime.now(timezone.utc)
    attempt.status = "running"
    attempt.phase = "execution"
    attempt.started_at = attempt.started_at or now
    attempt.heartbeat_at = now
    if process_id is not None:
        attempt.process_id = process_id
    await session.flush()
    return True


async def mark_attempt_running_token(
    execution_id: UUID, claim_token: UUID, *, process_id: str
) -> bool:
    """Persist child ownership immediately before its private-pipe dispatch."""

    from src.core.database import get_db_context

    async with get_db_context() as session:
        accepted = await mark_attempt_running(
            session, execution_id, claim_token, process_id=process_id
        )
        if accepted:
            await session.commit()
        return accepted


async def finalize_attempt(
    session: AsyncSession,
    execution_id: UUID,
    claim_token: UUID,
    *,
    status: str,
    phase: str,
    failure_code: str | None = None,
    failure_phase: str | None = None,
    duration_ms: int | None = None,
    peak_memory_bytes: int | None = None,
    cpu_total_seconds: float | None = None,
) -> bool:
    """Terminalize one active attempt using its unexposed capability token."""

    if status not in TERMINAL_ATTEMPT_STATUSES:
        raise ValueError(f"invalid terminal attempt status: {status}")
    if phase not in ATTEMPT_PHASES:
        raise ValueError(f"invalid attempt phase: {phase}")
    if failure_phase is not None and failure_phase not in FAILURE_PHASES:
        raise ValueError(f"invalid attempt failure phase: {failure_phase}")

    attempt = await session.scalar(
        select(ExecutionAttempt)
        .where(
            ExecutionAttempt.claim_token == claim_token,
            ExecutionAttempt.execution_id == execution_id,
            ExecutionAttempt.completed_at.is_(None),
        )
        .with_for_update()
    )
    if attempt is None:
        return False
    now = datetime.now(timezone.utc)
    attempt.status = status
    attempt.failure_phase = (
        None if status == "succeeded" else (failure_phase or attempt.phase)
    )
    attempt.phase = phase
    attempt.failure_code = failure_code
    attempt.duration_ms = duration_ms
    attempt.peak_memory_bytes = peak_memory_bytes
    attempt.cpu_total_seconds = cpu_total_seconds
    attempt.heartbeat_at = now
    attempt.completed_at = now
    await session.flush()
    return True


def failure_attempt_status(error_type: str) -> tuple[str, str]:
    """Map runtime error types to stable, non-sensitive attempt evidence."""

    if error_type == "TimeoutError":
        return "timed_out", "execution_timeout"
    if error_type == "CancelledError":
        return "cancelled", "cancelled"
    if error_type in {"ProcessCrashError", "OrphanedExecution", "WorkerShutdownError"}:
        return "worker_lost", "worker_process_lost"
    if error_type == "ResultPersistenceError":
        return "failed", "result_persist_failed"
    return "failed", "tenant_code_error"


async def heartbeat_attempt_tokens(claim_tokens: list[UUID]) -> set[UUID]:
    """Renew liveness evidence for active in-process attempts in one update."""

    if not claim_tokens:
        return set()
    from src.core.database import get_db_context

    now = datetime.now(timezone.utc)
    async with get_db_context() as session:
        result = await session.execute(
            update(ExecutionAttempt)
            .where(
                ExecutionAttempt.claim_token.in_(claim_tokens),
                ExecutionAttempt.completed_at.is_(None),
                ExecutionAttempt.status == "running",
            )
            .values(heartbeat_at=now)
            .returning(ExecutionAttempt.claim_token)
        )
        accepted = {row[0] for row in result.all()}
        await session.commit()
        return accepted
