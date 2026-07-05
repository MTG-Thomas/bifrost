from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from src.models.contracts.workflows import (
    RegisterWorkflowRequest,
    WorkflowUpdateRequest,
    WorkflowValidationRequest,
    WorkflowValidationResponse,
)
from src.models.contracts.executions import WorkflowExecutionRequest, WorkflowExecutionResponse
from src.models.enums import ExecutionStatus
from src.routers import workflows


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _Db:
    def __init__(self, *values):
        self.values = list(values)
        self.added = []
        self.flushed = False
        self.committed = False
        self.rolled_back = False

    async def execute(self, _stmt):
        if not self.values:
            raise AssertionError("unexpected execute call")
        return _ScalarResult(self.values.pop(0))

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, _value):
        return None


class _CancelDb:
    def __init__(self, row, rowcount: int = 1):
        self.row = row
        self.rowcount = rowcount
        self.committed = False
        self.refreshed = False

    async def get(self, _model, _execution_id):
        return self.row

    async def execute(self, _stmt):
        return SimpleNamespace(rowcount=self.rowcount)

    async def commit(self):
        self.committed = True

    async def refresh(self, _row):
        self.refreshed = True


def _admin(**overrides):
    data = {
        "email": "admin@example.com",
        "organization_id": uuid4(),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _ctx(user, org_id=None):
    return SimpleNamespace(user=user, org_id=org_id if org_id is not None else user.organization_id)


def _exec_user(**overrides):
    data = {
        "user_id": uuid4(),
        "email": "user@example.com",
        "name": "User",
        "organization_id": uuid4(),
        "is_superuser": False,
        "is_external": False,
        "embed": False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _workflow(**overrides):
    data = {
        "id": uuid4(),
        "name": "sync_records",
        "type": "workflow",
        "organization_id": None,
        "cache_ttl_seconds": 0,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class _WorkflowRepo:
    def __init__(self, workflow=None, access_error: Exception | None = None):
        self.workflow = workflow
        self.access_error = access_error
        self.resolve = AsyncMock(return_value=workflow)
        self.can_access = AsyncMock(side_effect=access_error)


@pytest.mark.asyncio
async def test_validate_workflow_returns_service_response_and_maps_errors() -> None:
    valid_response = WorkflowValidationResponse(valid=True, issues=[])

    with patch(
        "src.services.workflow_validation.validate_workflow_file",
        AsyncMock(return_value=valid_response),
    ) as validate:
        result = await workflows.validate_workflow(
            WorkflowValidationRequest(path="workflows/good.py", content="def run(): pass"),
            _admin(),
        )

    assert result is valid_response
    validate.assert_awaited_once_with(
        path="workflows/good.py",
        content="def run(): pass",
    )

    with patch(
        "src.services.workflow_validation.validate_workflow_file",
        AsyncMock(side_effect=ValueError("bad path")),
    ):
        with pytest.raises(HTTPException) as exc:
            await workflows.validate_workflow(
                WorkflowValidationRequest(path="../bad.py"),
                _admin(),
            )

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc.value.detail == "Invalid request: bad path"

    with patch(
        "src.services.workflow_validation.validate_workflow_file",
        AsyncMock(side_effect=RuntimeError("disk down")),
    ):
        with pytest.raises(HTTPException) as exc:
            await workflows.validate_workflow(
                WorkflowValidationRequest(path="workflows/bad.py"),
                _admin(),
            )

    assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc.value.detail == "Failed to validate workflow"


@pytest.mark.asyncio
async def test_register_workflow_reports_missing_file_and_non_python_path() -> None:
    missing_service = SimpleNamespace(read_file=AsyncMock(side_effect=FileNotFoundError()))

    with patch("src.services.file_storage.FileStorageService", return_value=missing_service):
        with pytest.raises(HTTPException) as exc:
            await workflows.register_workflow(
                RegisterWorkflowRequest(path="workflows/missing.py", function_name="run"),
                _Db(),
                _admin(),
            )

    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc.value.detail == "File not found: workflows/missing.py"

    service = SimpleNamespace(read_file=AsyncMock(return_value=(b"def run(): pass", None)))
    with patch("src.services.file_storage.FileStorageService", return_value=service):
        with pytest.raises(HTTPException) as exc:
            await workflows.register_workflow(
                RegisterWorkflowRequest(path="workflows/plain.txt", function_name="run"),
                _Db(),
                _admin(),
            )

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc.value.detail == "Path must be a .py file"


@pytest.mark.asyncio
async def test_register_workflow_reports_syntax_and_missing_decorator() -> None:
    service = SimpleNamespace(read_file=AsyncMock(return_value=(b"def run(: pass", None)))

    with patch("src.services.file_storage.FileStorageService", return_value=service):
        with pytest.raises(HTTPException) as exc:
            await workflows.register_workflow(
                RegisterWorkflowRequest(path="workflows/bad.py", function_name="run"),
                _Db(),
                _admin(),
            )

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "Syntax error:" in exc.value.detail

    service = SimpleNamespace(read_file=AsyncMock(return_value=(b"def run(): pass", None)))
    with patch("src.services.file_storage.FileStorageService", return_value=service):
        with pytest.raises(HTTPException) as exc:
            await workflows.register_workflow(
                RegisterWorkflowRequest(path="workflows/plain.py", function_name="run"),
                _Db(),
                _admin(),
            )

    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    assert "No decorated function 'run' found" in exc.value.detail


@pytest.mark.asyncio
async def test_register_workflow_rejects_invalid_access_role_and_duplicate() -> None:
    content = b"@workflow\ndef run(): pass"
    service = SimpleNamespace(read_file=AsyncMock(return_value=(content, None)))

    with patch("src.services.file_storage.FileStorageService", return_value=service):
        with pytest.raises(HTTPException) as exc:
            await workflows.register_workflow(
                RegisterWorkflowRequest(
                    path="workflows/run.py",
                    function_name="run",
                    access_level="bad",
                ),
                _Db(None),
                _admin(),
            )

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid access_level" in exc.value.detail

    with patch("src.services.file_storage.FileStorageService", return_value=service):
        with pytest.raises(HTTPException) as exc:
            await workflows.register_workflow(
                RegisterWorkflowRequest(
                    path="workflows/run.py",
                    function_name="run",
                    role_ids=["not-a-uuid"],
                ),
                _Db(None),
                _admin(),
            )

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid role ID" in exc.value.detail

    duplicate = SimpleNamespace(is_active=True)
    with patch("src.services.file_storage.FileStorageService", return_value=service):
        with pytest.raises(HTTPException) as exc:
            await workflows.register_workflow(
                RegisterWorkflowRequest(path="workflows/run.py", function_name="run"),
                _Db(duplicate),
                _admin(),
            )

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert exc.value.detail == "Workflow already registered"


@pytest.mark.asyncio
async def test_update_workflow_validates_name_access_and_methods() -> None:
    workflow_id = uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=None, name="run")

    for request, detail in (
        (WorkflowUpdateRequest(name=None), "name cannot be null"),
        (
            WorkflowUpdateRequest(access_level="invalid"),
            "Invalid access_level: 'invalid'. Must be 'authenticated', 'everyone', or 'role_based'",
        ),
        (
            WorkflowUpdateRequest(allowed_methods=["TRACE"]),
            "Invalid HTTP method: TRACE. Must be one of:",
        ),
    ):
        db = _Db(workflow)
        with (
            patch.object(workflows, "assert_not_solution_managed"),
            pytest.raises(HTTPException) as exc,
        ):
            await workflows.update_workflow(workflow_id, request, _admin(), db)

        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert detail in exc.value.detail
        assert db.committed is False


@pytest.mark.asyncio
async def test_execute_workflow_rejects_inline_code_for_non_admin() -> None:
    user = _exec_user(is_superuser=False)
    repo = _WorkflowRepo()

    with patch("src.repositories.WorkflowRepository", return_value=repo):
        with pytest.raises(HTTPException) as exc:
            await workflows.execute_workflow(
                WorkflowExecutionRequest(code="cHJpbnQoJ2hpJyk="),
                _ctx(user),
                _Db(),
                user,
            )

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail == "Inline code execution requires platform admin access"


@pytest.mark.asyncio
async def test_execute_workflow_maps_missing_and_denied_workflow() -> None:
    user = _exec_user()
    missing_repo = _WorkflowRepo(workflow=None)

    with patch("src.repositories.WorkflowRepository", return_value=missing_repo):
        with pytest.raises(HTTPException) as exc:
            await workflows.execute_workflow(
                WorkflowExecutionRequest(workflow_id="missing"),
                _ctx(user),
                _Db(),
                user,
            )

    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc.value.detail == "Workflow 'missing' not found"

    from src.repositories import AccessDeniedError

    workflow = _workflow()
    denied_repo = _WorkflowRepo(workflow=workflow, access_error=AccessDeniedError("denied"))
    with patch("src.repositories.WorkflowRepository", return_value=denied_repo):
        with pytest.raises(HTTPException) as exc:
            await workflows.execute_workflow(
                WorkflowExecutionRequest(workflow_id="sync_records"),
                _ctx(user),
                _Db(),
                user,
            )

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail == "Access denied to execute this workflow"
    denied_repo.can_access.assert_awaited_once_with(id=workflow.id)


@pytest.mark.asyncio
async def test_execute_workflow_schedules_workflow_with_delay() -> None:
    user = _exec_user(is_superuser=True)
    workflow = _workflow(organization_id=uuid4())
    repo = _WorkflowRepo(workflow=workflow)
    scheduled_id = uuid4()

    with (
        patch("src.repositories.WorkflowRepository", return_value=repo),
        patch.object(workflows, "_insert_scheduled_execution", AsyncMock(return_value=scheduled_id)) as insert_scheduled,
    ):
        result = await workflows.execute_workflow(
            WorkflowExecutionRequest(
                workflow_id="sync_records",
                input_data={"ticket": "123"},
                delay_seconds=60,
            ),
            _ctx(user),
            _Db(),
            user,
        )

    assert result.execution_id == str(scheduled_id)
    assert result.workflow_id == str(workflow.id)
    assert result.workflow_name == "sync_records"
    assert result.status == ExecutionStatus.SCHEDULED
    assert result.scheduled_at is not None
    insert_scheduled.assert_awaited_once()
    assert insert_scheduled.await_args.kwargs["parameters"] == {"ticket": "123"}
    assert insert_scheduled.await_args.kwargs["organization_id"] == workflow.organization_id


@pytest.mark.asyncio
async def test_execute_workflow_returns_cached_transient_data_provider_result() -> None:
    user = _exec_user()
    workflow = _workflow(type="data_provider", cache_ttl_seconds=300)
    repo = _WorkflowRepo(workflow=workflow)

    with (
        patch("src.repositories.WorkflowRepository", return_value=repo),
        patch.object(
            workflows,
            "get_cached_data_provider",
            AsyncMock(return_value={"data": [{"label": "One", "value": "1"}]}),
        ) as cached,
        patch("src.services.execution.service.run_workflow", AsyncMock()) as run_workflow,
    ):
        result = await workflows.execute_workflow(
            WorkflowExecutionRequest(
                workflow_id="options",
                input_data={"q": "o"},
                transient=True,
            ),
            _ctx(user),
            _Db(),
            user,
        )

    assert result.status == ExecutionStatus.SUCCESS
    assert result.result == [{"label": "One", "value": "1"}]
    assert result.is_transient is True
    cached.assert_awaited_once()
    run_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_workflow_translates_execution_service_errors() -> None:
    user = _exec_user(is_superuser=True)

    class WorkflowNotFoundError(Exception):
        pass

    class WorkflowLoadError(Exception):
        pass

    async def fail_not_found(**_kwargs):
        raise WorkflowNotFoundError("gone")

    async def fail_load(**_kwargs):
        raise WorkflowLoadError("bad import")

    async def fail_value(**_kwargs):
        raise ValueError("bad input")

    async def fail_unexpected(**_kwargs):
        raise RuntimeError("boom")

    import types

    service_mod = types.ModuleType("src.services.execution.service")
    service_mod.WorkflowNotFoundError = WorkflowNotFoundError
    service_mod.WorkflowLoadError = WorkflowLoadError
    service_mod.run_code = fail_not_found
    service_mod.run_workflow = AsyncMock()

    with patch.dict("sys.modules", {"src.services.execution.service": service_mod}):
        with pytest.raises(HTTPException) as exc:
            await workflows.execute_workflow(
                WorkflowExecutionRequest(code="cHJpbnQoJ2hpJyk="),
                _ctx(user),
                _Db(),
                user,
            )

    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc.value.detail == "gone"

    for runner, expected_status, expected_detail in (
        (fail_load, status.HTTP_500_INTERNAL_SERVER_ERROR, "bad import"),
        (fail_value, status.HTTP_400_BAD_REQUEST, "bad input"),
        (fail_unexpected, status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to execute workflow: RuntimeError: boom"),
    ):
        service_mod.run_code = runner
        with patch.dict("sys.modules", {"src.services.execution.service": service_mod}):
            with pytest.raises(HTTPException) as exc:
                await workflows.execute_workflow(
                    WorkflowExecutionRequest(code="cHJpbnQoJ2hpJyk="),
                    _ctx(user),
                    _Db(),
                    user,
                )

        assert exc.value.status_code == expected_status
        assert exc.value.detail == expected_detail


@pytest.mark.asyncio
async def test_cancel_scheduled_execution_returns_404_and_forbidden_branches() -> None:
    execution_id = uuid4()
    user = _admin(is_superuser=False, user_id=uuid4(), organization_id=uuid4())

    with pytest.raises(HTTPException) as exc:
        await workflows.cancel_scheduled_execution(execution_id, _ctx(user), _CancelDb(None), user)

    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc.value.detail == "Execution not found"

    other_org_row = SimpleNamespace(
        organization_id=uuid4(),
        executed_by=user.user_id,
        status=ExecutionStatus.SCHEDULED,
    )
    with pytest.raises(HTTPException) as exc:
        await workflows.cancel_scheduled_execution(
            execution_id,
            _ctx(user),
            _CancelDb(other_org_row),
            user,
        )

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail == "Access denied"

    other_owner_row = SimpleNamespace(
        organization_id=user.organization_id,
        executed_by=uuid4(),
        status=ExecutionStatus.SCHEDULED,
    )
    with pytest.raises(HTTPException) as exc:
        await workflows.cancel_scheduled_execution(
            execution_id,
            _ctx(user),
            _CancelDb(other_owner_row),
            user,
        )

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail == "Only the submitter or an admin may cancel"


@pytest.mark.asyncio
async def test_cancel_scheduled_execution_handles_success_and_race_conflict() -> None:
    execution_id = uuid4()
    user = _admin(is_superuser=False, user_id=uuid4(), organization_id=uuid4())
    row = SimpleNamespace(
        organization_id=user.organization_id,
        executed_by=user.user_id,
        status=ExecutionStatus.SCHEDULED,
    )
    db = _CancelDb(row, rowcount=1)

    result = await workflows.cancel_scheduled_execution(execution_id, _ctx(user), db, user)

    assert result == {
        "execution_id": str(execution_id),
        "status": ExecutionStatus.CANCELLED.value,
    }
    assert db.committed is True
    assert db.refreshed is False

    row.status = ExecutionStatus.PENDING
    conflict_db = _CancelDb(row, rowcount=0)
    with pytest.raises(HTTPException) as exc:
        await workflows.cancel_scheduled_execution(
            execution_id,
            _ctx(user),
            conflict_db,
            user,
        )

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert exc.value.detail == "Execution is not Scheduled (current status: Pending)"
    assert conflict_db.committed is True
    assert conflict_db.refreshed is True
