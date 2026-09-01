from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.workflows import (
    AssignRolesToWorkflowRequest,
    DeleteWorkflowRequest,
)
from src.routers import workflows


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value


class _RowCountResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class _Db:
    def __init__(self, *results):
        self.results = list(results)
        self.added = []
        self.flushed = False
        self.committed = False

    async def execute(self, _stmt):
        if not self.results:
            raise AssertionError("unexpected execute call")
        return self.results.pop(0)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True


def _admin():
    return SimpleNamespace(email="admin@example.com")


@pytest.mark.asyncio
async def test_get_workflow_roles_returns_assigned_role_ids():
    workflow_id = uuid4()
    role_ids = [uuid4(), uuid4()]
    db = _Db(_ScalarResult(workflow_id), _ScalarResult(role_ids))

    result = await workflows.get_workflow_roles(workflow_id, _admin(), db)

    assert result.role_ids == [str(role_id) for role_id in role_ids]


@pytest.mark.asyncio
async def test_get_workflow_roles_404s_when_workflow_missing():
    workflow_id = uuid4()
    db = _Db(_ScalarResult(None))

    with pytest.raises(HTTPException) as exc:
        await workflows.get_workflow_roles(workflow_id, _admin(), db)

    assert exc.value.status_code == 404
    assert str(workflow_id) in exc.value.detail


@pytest.mark.asyncio
async def test_assign_roles_adds_new_assignments_and_skips_existing():
    workflow_id = uuid4()
    new_role = uuid4()
    existing_role = uuid4()
    db = _Db(
        _ScalarResult(workflow_id),
        _ScalarResult(new_role),
        _ScalarResult(None),
        _ScalarResult(existing_role),
        _ScalarResult(object()),
    )

    with patch.object(
        workflows,
        "assert_entity_id_not_solution_managed",
        new=AsyncMock(),
    ) as guard:
        await workflows.assign_roles_to_workflow(
            workflow_id,
            AssignRolesToWorkflowRequest(
                role_ids=[str(new_role), str(existing_role)]
            ),
            _admin(),
            db,
        )

    guard.assert_awaited_once()
    assert db.flushed is True
    assert len(db.added) == 1
    assert db.added[0].workflow_id == workflow_id
    assert db.added[0].role_id == new_role
    assert db.added[0].assigned_by == "admin@example.com"


@pytest.mark.asyncio
async def test_assign_roles_404s_for_missing_workflow_before_guard():
    workflow_id = uuid4()
    db = _Db(_ScalarResult(None))

    with (
        patch.object(
            workflows,
            "assert_entity_id_not_solution_managed",
            new=AsyncMock(),
        ) as guard,
        pytest.raises(HTTPException) as exc,
    ):
        await workflows.assign_roles_to_workflow(
            workflow_id,
            AssignRolesToWorkflowRequest(role_ids=[str(uuid4())]),
            _admin(),
            db,
        )

    assert exc.value.status_code == 404
    guard.assert_not_awaited()


@pytest.mark.asyncio
async def test_assign_roles_404s_for_missing_role():
    workflow_id = uuid4()
    missing_role = uuid4()
    db = _Db(_ScalarResult(workflow_id), _ScalarResult(None))

    with (
        patch.object(
            workflows,
            "assert_entity_id_not_solution_managed",
            new=AsyncMock(),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await workflows.assign_roles_to_workflow(
            workflow_id,
            AssignRolesToWorkflowRequest(role_ids=[str(missing_role)]),
            _admin(),
            db,
        )

    assert exc.value.status_code == 404
    assert str(missing_role) in exc.value.detail
    assert db.flushed is False


@pytest.mark.asyncio
async def test_remove_role_from_workflow_checks_guard_and_deletes_assignment():
    workflow_id = uuid4()
    role_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        path="features/example.py",
        function_name="example",
    )
    db = _Db(_ScalarResult(workflow), _RowCountResult(1))

    with patch.object(
        workflows,
        "assert_entity_id_not_solution_managed",
        new=AsyncMock(),
    ) as guard:
        await workflows.remove_role_from_workflow(workflow_id, role_id, _admin(), db)

    guard.assert_awaited_once()


@pytest.mark.asyncio
async def test_remove_role_from_workflow_404s_when_assignment_missing():
    workflow_id = uuid4()
    role_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        path="features/example.py",
        function_name="example",
    )
    db = _Db(_ScalarResult(workflow), _RowCountResult(0))

    with (
        patch.object(
            workflows,
            "assert_entity_id_not_solution_managed",
            new=AsyncMock(),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await workflows.remove_role_from_workflow(workflow_id, role_id, _admin(), db)

    assert exc.value.status_code == 404
    assert exc.value.detail == "Workflow-role assignment not found"


@pytest.mark.asyncio
async def test_delete_workflow_404s_when_workflow_missing():
    workflow_id = uuid4()
    db = _Db(_ScalarResult(None))

    with pytest.raises(HTTPException) as exc:
        await workflows.delete_workflow(workflow_id, _admin(), db)

    assert exc.value.status_code == 404
    assert str(workflow_id) in exc.value.detail


@pytest.mark.asyncio
async def test_delete_workflow_rejects_workflow_without_source_path():
    workflow_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        name="No Path",
        path=None,
        solution_id=None,
    )
    db = _Db(_ScalarResult(workflow))

    with (
        patch.object(workflows, "assert_not_solution_managed"),
        pytest.raises(HTTPException) as exc,
    ):
        await workflows.delete_workflow(workflow_id, _admin(), db)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Workflow has no source file path — cannot delete"


@pytest.mark.asyncio
async def test_delete_workflow_returns_conflict_for_dependent_workflow():
    workflow_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        name="Delete Me",
        function_name="delete_me",
        path="workflows/delete_me.py",
        type="workflow",
        solution_id=None,
    )
    sibling = SimpleNamespace(
        id=uuid4(),
        function_name="keep_me",
        type="tool",
        name="Keep Me",
    )
    pending = SimpleNamespace(
        id=str(workflow_id),
        name=workflow.name,
        function_name=workflow.function_name,
        path=workflow.path,
        description=None,
        decorator_type="workflow",
        has_executions=False,
        last_execution_at=None,
        endpoint_enabled=False,
        affected_entities=[
            {
                "entity_type": "form",
                "id": str(uuid4()),
                "name": "Intake",
                "reference_type": "workflow",
            }
        ],
    )
    replacement = SimpleNamespace(
        function_name="keep_me",
        name="Keep Me",
        decorator_type="tool",
        similarity_score=0.75,
    )
    service = SimpleNamespace(
        detect_pending_deactivations=AsyncMock(
            return_value=([pending], [replacement])
        )
    )
    db = _Db(_ScalarResult(workflow), _ScalarResult([workflow, sibling]))

    with (
        patch.object(workflows, "assert_not_solution_managed"),
        patch(
            "src.services.file_storage.deactivation.DeactivationProtectionService",
            return_value=service,
        ),
    ):
        response = await workflows.delete_workflow(workflow_id, _admin(), db)

    assert response.status_code == 409
    assert b"workflows_would_deactivate" in response.body
    assert b"Intake" in response.body
    service.detect_pending_deactivations.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_workflow_deactivates_when_source_file_is_missing():
    workflow_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        name="Already Gone",
        function_name="gone",
        path="workflows/gone.py",
        type="workflow",
        solution_id=None,
        is_active=True,
    )
    db = _Db(_ScalarResult(workflow))
    file_service = SimpleNamespace(
        read_file=AsyncMock(side_effect=FileNotFoundError())
    )

    with (
        patch.object(workflows, "assert_not_solution_managed"),
        patch(
            "src.services.file_storage.FileStorageService",
            return_value=file_service,
        ),
    ):
        result = await workflows.delete_workflow(
            workflow_id,
            _admin(),
            db,
            DeleteWorkflowRequest(force_deactivation=True),
        )

    assert workflow.is_active is False
    assert db.committed is True
    assert result == {
        "status": "deleted",
        "detail": "Source file not found, workflow deactivated",
    }

@pytest.fixture(autouse=True)
def bypass_live_registration_authority(monkeypatch):
    """Router examples isolate role behavior from Workspace Live state."""
    monkeypatch.setattr(
        workflows,
        "_guard_workflow_registration_mutation",
        AsyncMock(),
    )
