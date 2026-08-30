"""Identity policy and atomic persistence for workflow registration."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models import Workflow as WorkflowORM
from src.services.workspace_release_registration_authority import (
    WorkspaceRegistrationMutationAuthority,
    guard_workspace_registration_mutation,
)


@dataclass(frozen=True)
class WorkspaceRegistrationCandidate:
    """A decorated Workspace function considered during changeset validation."""

    path: str
    function_name: str
    workflow_type: str
    name: str
    requested_id: str | None = None


def workspace_workflow_lookup_statement(
    organization_id: UUID,
    path: str,
    function_name: str,
    *,
    for_update: bool = False,
):
    """Prefer the organization override over a same-key global registration."""
    statement = (
        select(WorkflowORM)
        .where(
            WorkflowORM.path == path,
            WorkflowORM.function_name == function_name,
            WorkflowORM.solution_id.is_(None),
            or_(
                WorkflowORM.organization_id == organization_id,
                WorkflowORM.organization_id.is_(None),
            ),
        )
        .order_by(WorkflowORM.organization_id.is_(None).asc(), WorkflowORM.id.asc())
        .limit(1)
        .options(selectinload(WorkflowORM.roles))
    )
    return statement.with_for_update() if for_update else statement


async def find_workspace_workflow(
    db: AsyncSession,
    organization_id: UUID,
    path: str,
    function_name: str,
    *,
    for_update: bool = False,
) -> WorkflowORM | None:
    """Find a caller-visible global or organization-scoped Workspace row."""
    result = await db.execute(
        workspace_workflow_lookup_statement(
            organization_id, path, function_name, for_update=for_update
        )
    )
    return result.scalar_one_or_none()


async def list_active_workspace_workflows(
    db: AsyncSession,
    paths: Iterable[str],
    *,
    for_update: bool = False,
) -> list[WorkflowORM]:
    """Return every active mutable-Workspace registration on exact paths."""

    normalized_paths = sorted(set(paths))
    if not normalized_paths:
        return []
    statement = (
        select(WorkflowORM)
        .where(
            WorkflowORM.path.in_(normalized_paths),
            WorkflowORM.solution_id.is_(None),
            WorkflowORM.is_active.is_(True),
        )
        .order_by(WorkflowORM.path, WorkflowORM.function_name, WorkflowORM.id)
        .options(selectinload(WorkflowORM.roles))
    )
    if for_update:
        statement = statement.with_for_update()
    return list((await db.execute(statement)).scalars().all())


def _planned_action(existing: WorkflowORM | None) -> str:
    """Describe the registry mutation implied by the current row state."""
    if existing is None:
        return "create"
    return "preserve" if existing.is_active else "reactivate"


def _planned_organization_id(
    existing: WorkflowORM | None, organization_id: UUID
) -> str | None:
    """Preserve global ownership; otherwise bind a new row to the caller."""
    if existing is not None and existing.organization_id is None:
        return None
    return str(existing.organization_id if existing is not None else organization_id)


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
    for candidate in sorted(
        candidates, key=lambda item: (item.path, item.function_name)
    ):
        existing = await find_workspace_workflow(
            db, organization_id, candidate.path, candidate.function_name
        )
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

        actions.append(
            {
                "action": _planned_action(existing),
                "path": candidate.path,
                "function_name": candidate.function_name,
                "type": candidate.workflow_type,
                "name": candidate.name,
                "requested_id": str(requested_id) if requested_id else None,
                "organization_id": _planned_organization_id(existing, organization_id),
            }
        )
    return actions, diagnostics


async def apply_workspace_registration_plan(
    db: AsyncSession,
    organization_id: UUID,
    actions: list[dict],
    *,
    authority: WorkspaceRegistrationMutationAuthority = (
        WorkspaceRegistrationMutationAuthority.EXTERNAL
    ),
) -> list[dict]:
    """Create missing Workspace rows in the caller's activation transaction.

    Existing inactive rows are reactivated here so exact-byte registration-only
    activation does not depend on a source rewrite. A subsequent source write may
    still let ``WorkflowIndexer`` enrich the row from the activated file.
    """
    await guard_workspace_registration_mutation(
        db,
        operation="apply workflow registration plan",
        paths=(action["path"] for action in actions),
        authority=authority,
    )
    applied: list[dict] = []
    for action in actions:
        existing = await find_workspace_workflow(
            db, organization_id, action["path"], action["function_name"]
        )
        _assert_registration_plan_current(action, existing)
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
            if action.get("action") == "reactivate":
                existing.is_active = True
            existing.name = action.get("name") or action["function_name"]
            existing.type = action["type"]
            await db.flush()
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


def _assert_registration_plan_current(
    action: dict, existing: WorkflowORM | None
) -> None:
    """Reject activation when registry state changed after preview."""
    expected_action = action.get("action")
    ref = f"{action['path']}::{action['function_name']}"
    if expected_action == "create" and existing is not None:
        raise WorkflowRegistrationConflict(
            f"registration plan became stale for {ref}: expected a new registry row"
        )
    if expected_action in {"preserve", "reactivate"} and existing is None:
        raise WorkflowRegistrationConflict(
            f"registration plan became stale for {ref}: expected registry row is missing"
        )
    active_state_changed = existing is not None and (
        (expected_action == "preserve" and not existing.is_active)
        or (expected_action == "reactivate" and existing.is_active)
    )
    if active_state_changed:
        raise WorkflowRegistrationConflict(
            f"registration plan became stale for {ref}: active state changed"
        )


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
