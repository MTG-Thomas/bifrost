"""Identity policy and atomic persistence for explicit workflow registration."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Workflow as WorkflowORM


class WorkflowRegistrationIdInvalid(ValueError):
    """A caller supplied a workflow identifier that is not a UUID."""


class WorkflowRegistrationConflict(ValueError):
    """A requested workflow identity conflicts with an existing registration."""


async def resolve_workflow_registration_id(
    db: AsyncSession,
    requested_id: str | None,
    existing_workflow: WorkflowORM | None,
) -> UUID | None:
    """Validate an optional portable ID and reject known ownership conflicts."""
    if requested_id is None:
        return None

    try:
        workflow_id = UUID(requested_id)
    except (ValueError, AttributeError) as exc:
        raise WorkflowRegistrationIdInvalid(
            f"Invalid workflow ID: '{requested_id}' (expected a UUID)"
        ) from exc

    if existing_workflow and existing_workflow.id != workflow_id:
        raise WorkflowRegistrationConflict(
            f"Workflow '{existing_workflow.function_name}' in "
            f"{existing_workflow.path} is already registered with UUID "
            f"{existing_workflow.id}, not {workflow_id}"
        )

    result = await db.execute(select(WorkflowORM).where(WorkflowORM.id == workflow_id))
    owner = result.scalar_one_or_none()
    if owner and (not existing_workflow or owner.id != existing_workflow.id):
        raise WorkflowRegistrationConflict(
            f"Workflow UUID {workflow_id} is already used by "
            f"{owner.path}::{owner.function_name}"
        )
    return workflow_id


async def add_workflow_registration(
    db: AsyncSession,
    workflow: WorkflowORM,
    requested_workflow_id: UUID | None,
) -> None:
    """Insert inside a savepoint and translate identity races into conflicts."""
    try:
        async with db.begin_nested():
            db.add(workflow)
            await db.flush()
    except IntegrityError as exc:
        # The primary key and path/function indexes are the final authority.
        # Re-read after the savepoint rollback so concurrent registrations get
        # the same useful conflict response as pre-existing registrations.
        if requested_workflow_id is not None:
            result = await db.execute(
                select(WorkflowORM).where(WorkflowORM.id == requested_workflow_id)
            )
            owner = result.scalar_one_or_none()
            if owner:
                raise WorkflowRegistrationConflict(
                    f"Workflow UUID {requested_workflow_id} is already used by "
                    f"{owner.path}::{owner.function_name}"
                ) from exc

        result = await db.execute(
            select(WorkflowORM).where(
                WorkflowORM.path == workflow.path,
                WorkflowORM.function_name == workflow.function_name,
                WorkflowORM.solution_id.is_(None),
            )
        )
        owner = result.scalar_one_or_none()
        if owner:
            raise WorkflowRegistrationConflict(
                f"Workflow '{workflow.function_name}' in {workflow.path} is "
                f"already registered with UUID {owner.id}"
            ) from exc
        raise
