"""Safe recovery for client-identified workflow submissions."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.executions import WorkflowExecutionResponse
from src.models.orm.executions import Execution


class ExecutionSubmissionConflictError(ValueError):
    """Raised when a submission identity is reused for different work."""


async def recover_execution_submission(
    db: AsyncSession,
    *,
    execution_id: UUID,
    workflow_id: UUID,
    parameters: dict,
    executed_by: UUID,
    organization_id: UUID | None,
    form_id: UUID | None,
) -> WorkflowExecutionResponse | None:
    """Return a matching persisted execution or reject identity reuse.

    The caller-generated execution UUID is both the submission identity and the
    durable execution primary key.  That lets a client recover with a read-only
    ``GET /api/executions/{id}`` after losing the POST response, without
    replaying the mutation.
    """

    existing = await db.get(Execution, execution_id)
    if existing is None:
        return None

    expected = (
        workflow_id,
        parameters,
        executed_by,
        organization_id,
        form_id,
    )
    actual = (
        existing.workflow_id,
        existing.parameters or {},
        existing.executed_by,
        existing.organization_id,
        existing.form_id,
    )
    if actual != expected:
        raise ExecutionSubmissionConflictError(
            "execution submission identity is already bound to different work"
        )

    return WorkflowExecutionResponse(
        execution_id=str(existing.id),
        workflow_id=str(existing.workflow_id) if existing.workflow_id else None,
        workflow_name=existing.workflow_name,
        status=existing.status,
        result=existing.result,
        error=existing.error_message,
        duration_ms=existing.duration_ms,
        started_at=existing.started_at,
        completed_at=existing.completed_at,
        scheduled_at=existing.scheduled_at,
    )
