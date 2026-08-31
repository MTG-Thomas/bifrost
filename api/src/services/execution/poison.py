"""Durable finalization for workflow messages moved to the poison queue."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text

from src.core.database import get_db_context
from src.core.pubsub import publish_execution_update, publish_history_update
from src.core.redis_client import get_redis_client
from src.models.enums import ExecutionStatus
from src.models.orm.executions import (
    Execution,
    ExecutionLog,
    WorkflowExecutionAttempt as ExecutionAttempt,
)

logger = logging.getLogger(__name__)


class PoisonFinalizationConflict(RuntimeError):
    """The durable execution does not match the requested poison transition."""


@dataclass(frozen=True)
class PoisonFinalizationResult:
    execution_id: str
    disposition: str
    status: str | None
    transient_state_cleaned: bool


@dataclass(frozen=True)
class _DurablePoisonResult:
    disposition: str
    status: str | None
    completed_at: datetime
    summary: dict | None


async def _record_durable_poison(
    *,
    execution_uuid: UUID,
    execution_id: str,
    queue: str,
    reason: str,
    retry_count: int,
    replay_count: int,
    message_id: str | None,
    operation: str,
    require_matching_terminal: bool,
) -> _DurablePoisonResult:
    completed_at = datetime.now(timezone.utc)
    error_message = f"Execution delivery moved to poison queue: {reason}"
    disposition = "missing"
    status: str | None = None
    summary: dict | None = None

    async with get_db_context() as db:
        await db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('bifrost:workflow-execution:' || :execution_id))"
            ),
            {"execution_id": execution_id},
        )
        execution = await db.get(Execution, execution_uuid)
        if execution is None:
            if require_matching_terminal:
                raise PoisonFinalizationConflict("durable execution does not exist")
        elif execution.status in {
            ExecutionStatus.SCHEDULED,
            ExecutionStatus.PENDING,
            ExecutionStatus.RUNNING,
            ExecutionStatus.CANCELLING,
        }:
            original_status = ExecutionStatus(execution.status)
            final_status = (
                ExecutionStatus.CANCELLED
                if original_status == ExecutionStatus.CANCELLING
                else ExecutionStatus.FAILED
            )
            active_attempt = await db.scalar(
                select(ExecutionAttempt)
                .where(
                    ExecutionAttempt.execution_id == execution_uuid,
                    ExecutionAttempt.completed_at.is_(None),
                )
                .with_for_update()
            )
            if active_attempt is not None:
                active_attempt.status = (
                    "cancelled"
                    if final_status == ExecutionStatus.CANCELLED
                    else "failed"
                )
                active_attempt.phase = "terminal"
                active_attempt.failure_phase = (
                    "cancellation"
                    if final_status == ExecutionStatus.CANCELLED
                    else "queue"
                )
                active_attempt.failure_code = "consumer_delivery_poisoned"
                active_attempt.completed_at = completed_at
                active_attempt.heartbeat_at = completed_at
            execution.status = final_status
            execution.error_message = error_message
            execution.completed_at = completed_at
            db.add(
                ExecutionLog(
                    execution_id=execution_uuid,
                    level="error",
                    message=error_message,
                    log_metadata={
                        "schema": "bifrost.execution-poison/v1",
                        "operation": operation,
                        "queue": queue,
                        "reason": reason,
                        "retry_count": retry_count,
                        "replay_count": replay_count,
                        "message_id": message_id,
                        "original_status": original_status.value,
                        "final_status": final_status.value,
                    },
                    timestamp=completed_at,
                )
            )
            await db.commit()
            disposition = "terminalized"
            status = final_status.value
            summary = {
                "executed_by": execution.executed_by,
                "executed_by_name": execution.executed_by_name,
                "workflow_name": execution.workflow_name,
                "org_id": execution.organization_id,
                "started_at": execution.started_at,
            }
        else:
            status = ExecutionStatus(execution.status).value
            if require_matching_terminal and execution.error_message != error_message:
                raise PoisonFinalizationConflict(
                    f"durable execution is already {status} with different evidence"
                )
            disposition = "already_terminal"

    return _DurablePoisonResult(
        disposition=disposition,
        status=status,
        completed_at=completed_at,
        summary=summary,
    )


async def _cleanup_transient_poison_state(
    *,
    execution_id: str,
    status: str | None,
    error_message: str,
    sync: bool,
    required: bool,
) -> bool:
    redis_client = get_redis_client()
    try:
        from src.services.execution.queue_tracker import remove_from_queue

        await remove_from_queue(execution_id)
        if sync:
            await redis_client.push_result(
                execution_id=execution_id,
                status=status or ExecutionStatus.FAILED.value,
                error=error_message,
                error_type="ConsumerDeliveryPoisoned",
                duration_ms=0,
            )
        await redis_client.delete_pending_execution(execution_id)
        return True
    except Exception as exc:
        if required:
            raise PoisonFinalizationConflict(
                f"durable poison is recorded but Redis cleanup failed: {exc}"
            ) from exc
        logger.warning(
            "Poisoned execution %s is durable but Redis cleanup is deferred: %s",
            execution_id,
            exc,
        )
        return False


async def _publish_poison_update(
    *,
    execution_id: str,
    status: str,
    error_message: str,
    completed_at: datetime,
    summary: dict,
) -> None:
    try:
        await publish_execution_update(
            execution_id,
            status,
            {
                "error": error_message,
                "errorType": "ConsumerDeliveryPoisoned",
            },
        )
        await publish_history_update(
            execution_id=execution_id,
            status=status,
            executed_by=summary["executed_by"],
            executed_by_name=summary["executed_by_name"],
            workflow_name=summary["workflow_name"],
            org_id=summary["org_id"],
            started_at=summary["started_at"],
            completed_at=completed_at,
        )
    except Exception as exc:
        logger.warning(
            "Poisoned execution %s is durable but its live update could not be published: %s",
            execution_id,
            exc,
        )


async def finalize_poisoned_execution(
    *,
    execution_id: str,
    queue: str,
    reason: str,
    retry_count: int,
    replay_count: int,
    message_id: str | None,
    sync: bool,
    operation: str = "consumer_poison",
    require_matching_terminal: bool = False,
    require_transient_cleanup: bool = False,
) -> PoisonFinalizationResult:
    """Persist a poison outcome and remove transient execution state.

    PostgreSQL status and audit evidence commit together under the execution's
    advisory lock. Redis cleanup follows the durable commit and can be retried
    idempotently after an outage.
    """
    try:
        execution_uuid = UUID(execution_id)
    except ValueError as exc:
        raise PoisonFinalizationConflict("execution_id is not a UUID") from exc

    error_message = f"Execution delivery moved to poison queue: {reason}"
    durable = await _record_durable_poison(
        execution_uuid=execution_uuid,
        execution_id=execution_id,
        queue=queue,
        reason=reason,
        retry_count=retry_count,
        replay_count=replay_count,
        message_id=message_id,
        operation=operation,
        require_matching_terminal=require_matching_terminal,
    )
    transient_state_cleaned = await _cleanup_transient_poison_state(
        execution_id=execution_id,
        status=durable.status,
        error_message=error_message,
        sync=sync,
        required=require_transient_cleanup,
    )
    if (
        durable.disposition == "terminalized"
        and durable.summary is not None
        and durable.status is not None
    ):
        await _publish_poison_update(
            execution_id=execution_id,
            status=durable.status,
            error_message=error_message,
            completed_at=durable.completed_at,
            summary=durable.summary,
        )

    return PoisonFinalizationResult(
        execution_id=execution_id,
        disposition=durable.disposition,
        status=durable.status,
        transient_state_cleaned=transient_state_cleaned,
    )
