"""Workspace registration planning stays explicit and transaction-friendly."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import src.services.workflow_registration as workflow_registration

from src.services.workflow_registration import (
    WorkspaceRegistrationCandidate,
    WorkflowRegistrationConflict,
    apply_workspace_registration_plan,
    plan_workspace_registrations,
    workspace_workflow_lookup_statement,
)
from src.services.workspace_release_registration_authority import (
    WorkspaceRegistrationMutationAuthority,
)


def _result(value):
    return SimpleNamespace(scalar_one_or_none=lambda: value)


def test_lookup_prefers_org_override_before_same_key_global_registration():
    statement = workspace_workflow_lookup_statement(
        uuid4(), "features/demo.py", "run"
    )
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))

    assert "organization_id IS NULL ASC" in sql
    assert "LIMIT 1" in sql


@pytest.mark.asyncio
async def test_apply_defaults_to_external_live_authority(monkeypatch):
    blocked = RuntimeError("governed")
    guard = AsyncMock(side_effect=blocked)
    monkeypatch.setattr(
        workflow_registration,
        "guard_workspace_registration_mutation",
        guard,
    )
    actions = [
        {
            "action": "create",
            "path": "features/live.py",
            "function_name": "run",
            "type": "workflow",
            "name": "Live",
            "requested_id": None,
        }
    ]

    with pytest.raises(RuntimeError, match="governed"):
        await apply_workspace_registration_plan(SimpleNamespace(), uuid4(), actions)

    assert guard.await_args.kwargs["authority"] is (
        WorkspaceRegistrationMutationAuthority.EXTERNAL
    )
    assert list(guard.await_args.kwargs["paths"]) == ["features/live.py"]


@pytest.mark.asyncio
async def test_plan_reports_create_and_preserve_in_stable_order():
    organization_id = uuid4()
    existing = SimpleNamespace(
        id=uuid4(),
        is_active=True,
        organization_id=None,
        path="features/z.py",
        function_name="zeta",
    )
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_result(None), _result(existing)])
    )

    actions, diagnostics = await plan_workspace_registrations(
        db,
        organization_id,
        [
            WorkspaceRegistrationCandidate(
                path="features/z.py",
                function_name="zeta",
                workflow_type="workflow",
                name="Zeta",
            ),
            WorkspaceRegistrationCandidate(
                path="features/a.py",
                function_name="alpha",
                workflow_type="data_provider",
                name="Alpha",
            ),
        ],
    )

    assert diagnostics == []
    assert [(item["function_name"], item["action"]) for item in actions] == [
        ("alpha", "create"),
        ("zeta", "preserve"),
    ]
    assert actions[0]["organization_id"] == str(organization_id)
    assert actions[1]["organization_id"] is None


@pytest.mark.asyncio
async def test_plan_turns_portable_id_ownership_conflict_into_diagnostic():
    requested_id = uuid4()
    owner = SimpleNamespace(
        id=requested_id,
        path="features/owner.py",
        function_name="owner",
    )
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_result(None), _result(owner)])
    )

    actions, diagnostics = await plan_workspace_registrations(
        db,
        uuid4(),
        [
            WorkspaceRegistrationCandidate(
                path="features/candidate.py",
                function_name="candidate",
                workflow_type="tool",
                name="Candidate",
                requested_id=str(requested_id),
            )
        ],
    )

    assert actions == []
    assert diagnostics[0]["source"] == "registry_identity"
    assert "already used" in diagnostics[0]["message"]


@pytest.mark.asyncio
async def test_plan_reactivates_an_inactive_existing_row():
    organization_id = uuid4()
    existing = SimpleNamespace(
        id=uuid4(),
        is_active=False,
        organization_id=organization_id,
        path="features/dormant.py",
        function_name="dormant",
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_result(existing)))

    actions, diagnostics = await plan_workspace_registrations(
        db,
        organization_id,
        [
            WorkspaceRegistrationCandidate(
                path=existing.path,
                function_name=existing.function_name,
                workflow_type="workflow",
                name="Dormant",
            )
        ],
    )

    assert diagnostics == []
    assert actions[0]["action"] == "reactivate"


class _RegistrationDB:
    def __init__(self):
        self.added = []

    async def execute(self, _statement):
        return _result(None)

    @asynccontextmanager
    async def begin_nested(self):
        yield

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_apply_assigns_identity_inside_callers_transaction():
    organization_id = uuid4()
    db = _RegistrationDB()

    applied = await apply_workspace_registration_plan(
        db,
        organization_id,
        [
            {
                "action": "create",
                "path": "features/new.py",
                "function_name": "new_workflow",
                "type": "workflow",
                "name": "New workflow",
                "requested_id": None,
                "organization_id": str(organization_id),
            }
        ],
        authority=WorkspaceRegistrationMutationAuthority.RELEASE_ACTIVATION,
    )

    assert applied[0]["workflow_id"] == str(db.added[0].id)
    assert applied[0]["action"] == "create"
    assert db.added[0].organization_id == organization_id
    assert db.added[0].access_level == "role_based"
    assert db.added[0].function_name == "new_workflow"


@pytest.mark.asyncio
async def test_apply_reactivates_existing_row_without_rewriting_source():
    organization_id = uuid4()
    existing = SimpleNamespace(
        id=uuid4(),
        is_active=False,
        organization_id=organization_id,
        path="features/dormant.py",
        function_name="dormant",
        name="Old name",
        type="workflow",
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_result(existing)),
        flush=AsyncMock(),
    )

    applied = await apply_workspace_registration_plan(
        db,
        organization_id,
        [
            {
                "action": "reactivate",
                "path": existing.path,
                "function_name": existing.function_name,
                "type": "tool",
                "name": "Dormant tool",
                "requested_id": None,
                "organization_id": str(organization_id),
            }
        ],
        authority=WorkspaceRegistrationMutationAuthority.RELEASE_ACTIVATION,
    )

    assert existing.is_active is True
    assert existing.name == "Dormant tool"
    assert existing.type == "tool"
    assert applied[0]["workflow_id"] == str(existing.id)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_rejects_registry_state_that_changed_after_plan():
    existing = SimpleNamespace(
        id=uuid4(),
        is_active=True,
        organization_id=uuid4(),
        path="features/new.py",
        function_name="new_workflow",
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_result(existing)))

    with pytest.raises(WorkflowRegistrationConflict, match="expected a new"):
        await apply_workspace_registration_plan(
            db,
            uuid4(),
            [
                {
                    "action": "create",
                    "path": existing.path,
                    "function_name": existing.function_name,
                    "type": "workflow",
                    "name": "New workflow",
                    "requested_id": None,
                }
            ],
            authority=WorkspaceRegistrationMutationAuthority.RELEASE_ACTIVATION,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("planned_action", "current_active"),
    [("preserve", False), ("reactivate", True)],
)
async def test_apply_rejects_changed_active_state(
    planned_action, current_active
):
    organization_id = uuid4()
    existing = SimpleNamespace(
        id=uuid4(),
        is_active=current_active,
        organization_id=organization_id,
        path="features/stateful.py",
        function_name="stateful",
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_result(existing)))

    with pytest.raises(WorkflowRegistrationConflict, match="active state changed"):
        await apply_workspace_registration_plan(
            db,
            organization_id,
            [
                {
                    "action": planned_action,
                    "path": existing.path,
                    "function_name": existing.function_name,
                    "type": "workflow",
                    "name": "Stateful",
                    "requested_id": None,
                }
            ],
            authority=WorkspaceRegistrationMutationAuthority.RELEASE_ACTIVATION,
        )
