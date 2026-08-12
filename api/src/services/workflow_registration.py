"""Identity policy and atomic persistence for workflow registration."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Workflow as WorkflowORM


@dataclass(frozen=True)
class WorkspaceRegistrationCandidate:
    """A decorated Workspace function considered during changeset validation."""

    path: str
    function_name: str
    workflow_type: str
    name: str
    requested_id: str | None = None


async def plan_workspace_registrations(
    db: AsyncSession,
    organization_id: UUID,
    candidates: list[WorkspaceRegistrationCandidate],
) -> tuple[list[dict], list[dict]]:
    """Return deterministic registry actions and identity diagnostics.

    Workspace source is only executable when the decorated function has a registry
    row.  Planning that row alongside the source mutation prevents a successful file
    release from silently producing a 404 at execution time.
    """
    actions: list[dict] = []
    diagnostics: list[dict] = []
    for candidate in sorted(candidates, key=lambda item: (item.path, item.function_name)):
        result = await db.execute(
            select(WorkflowORM).where(
                WorkflowORM.path == candidate.path,
                WorkflowORM.function_name == candidate.function_name,
                WorkflowORM.solution_id.is_(None),
            )
        )
        existing = result.scalar_one_or_none()
        try:
            requested_id = await resolve_workflow_registration_id(
                db, candidate.requested_id, existing
            )
        except (WorkflowRegistrationIdInvalid, WorkflowRegistrationConflict) as exc:
            diagnostics.append(
                {
                    "path": candidate.path,
                    "function_name": candidate.function_name,
                    "severity": "error",
                    "source": "registry_identity",
                    "message": str(exc),
                }
            )
            continue

        if existing is not None and existing.is_active:
            action = "preserve"
        elif existing is not None:
            action = "reactivate"
        else:
            action = "create"
        actions.append(
            {
                "action": action,
                "path": candidate.path,
                "function_name": candidate.function_name,
                "type": candidate.workflow_type,
                "name": candidate.name,
                "requested_id": str(requested_id) if requested_id else None,
                "organization_id": str(
                    existing.organization_id if existing is not None else organization_id
                )
                if (existing is None or existing.organization_id is not None)
                else None,
            }
        )
    return actions, diagnostics


async def apply_workspace_registration_plan(
    db: AsyncSession,
    organization_id: UUID,
    actions: list[dict],
) -> list[dict]:
    """Create missing Workspace rows in the caller's activation transaction.

    Existing rows are deliberately left for ``WorkflowIndexer`` to enrich and
    reactivate after the corresponding source file is written.
    """
    applied: list[dict] = []
    for action in actions:
        result = await db.execute(
            select(WorkflowORM).where(
                WorkflowORM.path == action["path"],
                WorkflowORM.function_name == action["function_name"],
                WorkflowORM.solution_id.is_(None),
            )
        )
        existing = result.scalar_one_or_none()
        expected_action = action.get("action")
        if expected_action == "create" and existing is not None:
            raise WorkflowRegistrationConflict(
                f"registration plan became stale for {action['path']}::"
                f"{action['function_name']}: expected a new registry row"
            )
        if expected_action in {"preserve", "reactivate"} and existing is None:
            raise WorkflowRegistrationConflict(
                f"registration plan became stale for {action['path']}::"
                f"{action['function_name']}: expected registry row is missing"
            )
        if (
            expected_action == "preserve"
            and existing is not None
            and not existing.is_active
        ) or (
            expected_action == "reactivate"
            and existing is not None
            and existing.is_active
        ):
            raise WorkflowRegistrationConflict(
                f"registration plan became stale for {action['path']}::"
                f"{action['function_name']}: active state changed"
            )
        try:
            requested_id = await resolve_workflow_registration_id(
                db, action.get("requested_id"), existing
            )
        except (WorkflowRegistrationIdInvalid, WorkflowRegistrationConflict) as exc:
            raise WorkflowRegistrationConflict(
                f"registration plan became stale for {action['path']}::"
                f"{action['function_name']}: {exc}"
            ) from exc
        if existing is not None:
            applied.append({**action, "workflow_id": str(existing.id)})
            continue

        workflow_id = requested_id or uuid4()
        workflow = WorkflowORM(
            id=workflow_id,
            name=action.get("name") or action["function_name"],
            function_name=action["function_name"],
            path=action["path"],
            type=action["type"],
            is_active=True,
            organization_id=organization_id,
            access_level="role_based",
        )
        await add_workflow_registration(db, workflow, requested_id)
        applied.append({**action, "workflow_id": str(workflow_id)})
    return applied


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
