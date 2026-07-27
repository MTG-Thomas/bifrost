from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

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
    def __init__(self, *values, flush_error=None):
        self.values = list(values)
        self.added = []
        self.flushed = False
        self.committed = False
        self.rolled_back = False
        self.flush_error = flush_error

    async def execute(self, _stmt):
        if not self.values:
            raise AssertionError("unexpected execute call")
        return _ScalarResult(self.values.pop(0))

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushed = True
        if self.flush_error is not None:
            error = self.flush_error
            self.flush_error = None
            raise error

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, _value):
        return None

    def begin_nested(self):
        class _Savepoint:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *_args):
                return None

        return _Savepoint()


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
        "is_provider_org": False,
        "app_id": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _ctx(user, org_id=None):
    return SimpleNamespace(
        user=user,
        org_id=org_id if org_id is not None else user.organization_id,
        solution_id=None,
        app_id=None,
    )


def _exec_user(**overrides):
    data = {
        "user_id": uuid4(),
        "email": "user@example.com",
        "name": "User",
        "organization_id": uuid4(),
        "is_superuser": False,
        "is_provider_org": False,
        "is_external": False,
        "embed": False,
        "app_id": None,
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
async def test_register_workflow_validates_and_preserves_promoted_uuid() -> None:
    content = b"@workflow\ndef run(): pass"
    service = SimpleNamespace(read_file=AsyncMock(return_value=(content, None)))
    invalid_request = RegisterWorkflowRequest(
        path="workflows/run.py",
        function_name="run",
        id="not-a-uuid",
    )

    with patch("src.services.file_storage.FileStorageService", return_value=service):
        with pytest.raises(HTTPException) as exc:
            await workflows.register_workflow(
                invalid_request,
                _Db(None),
                _admin(),
            )

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid workflow ID" in exc.value.detail

    empty_request = RegisterWorkflowRequest(
        path="workflows/run.py",
        function_name="run",
        id="",
    )
    with patch("src.services.file_storage.FileStorageService", return_value=service):
        with pytest.raises(HTTPException) as exc:
            await workflows.register_workflow(
                empty_request,
                _Db(None),
                _admin(),
            )

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid workflow ID" in exc.value.detail

    requested_id = uuid4()
    collision = SimpleNamespace(
        id=requested_id,
        path="workflows/other.py",
        function_name="other",
    )
    collision_request = RegisterWorkflowRequest(
        path="workflows/run.py",
        function_name="run",
        id=str(requested_id),
    )
    with patch("src.services.file_storage.FileStorageService", return_value=service):
        with pytest.raises(HTTPException) as exc:
            await workflows.register_workflow(
                collision_request,
                _Db(None, collision),
                _admin(),
            )

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert "already used by workflows/other.py::other" in exc.value.detail

    path_owner = SimpleNamespace(
        id=uuid4(),
        path="workflows/run.py",
        function_name="run",
    )
    with patch("src.services.file_storage.FileStorageService", return_value=service):
        with pytest.raises(HTTPException) as exc:
            await workflows.register_workflow(
                collision_request,
                _Db(path_owner),
                _admin(),
            )

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert f"already registered with UUID {path_owner.id}" in exc.value.detail

    race_owner = SimpleNamespace(
        id=requested_id,
        path="workflows/racing.py",
        function_name="racing",
    )
    race_error = IntegrityError("INSERT", {}, Exception("duplicate key"))
    with patch("src.services.file_storage.FileStorageService", return_value=service):
        with pytest.raises(HTTPException) as exc:
            await workflows.register_workflow(
                collision_request,
                _Db(None, None, race_owner, flush_error=race_error),
                _admin(),
            )

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert "already used by workflows/racing.py::racing" in exc.value.detail

    created = SimpleNamespace(
        id=requested_id,
        name="run",
        function_name="run",
        path="workflows/run.py",
        type="workflow",
        description=None,
        organization_id=None,
    )
    db = _Db(None, None, created)
    indexer = SimpleNamespace(index_python_file=AsyncMock())
    with (
        patch("src.services.file_storage.FileStorageService", return_value=service),
        patch(
            "src.services.file_storage.indexers.workflow.WorkflowIndexer",
            return_value=indexer,
        ),
        patch("src.services.mcp_server.server.refresh_workflow_tools", AsyncMock()),
    ):
        result = await workflows.register_workflow(
            RegisterWorkflowRequest(
                path="workflows/run.py",
                function_name="run",
                id=str(requested_id),
                organization_id=None,
            ),
            db,
            _admin(),
        )

    assert result.id == str(requested_id)
    assert db.added[0].id == requested_id
    assert db.committed is True
    indexer.index_python_file.assert_awaited_once()


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
    assert exc.value.detail["message"] == "Workflow 'missing' not found"

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
async def test_execute_workflow_returns_existing_matching_submission() -> None:
    user = _exec_user()
    workflow = _workflow()
    repo = _WorkflowRepo(workflow=workflow)
    execution_id = uuid4()
    existing = WorkflowExecutionResponse(
        execution_id=str(execution_id),
        workflow_id=str(workflow.id),
        workflow_name=workflow.name,
        status=ExecutionStatus.SUCCESS,
        result={"covered": True},
    )

    with (
        patch("src.repositories.WorkflowRepository", return_value=repo),
        patch(
            "src.services.execution.submission_recovery.recover_execution_submission",
            AsyncMock(return_value=existing),
        ) as recover,
        patch("src.services.execution.service.run_workflow", AsyncMock()) as run_workflow,
    ):
        result = await workflows.execute_workflow(
            WorkflowExecutionRequest(
                workflow_id="sync_records",
                input_data={"ring": 3},
            ),
            _ctx(user),
            _Db(),
            user,
            execution_request_id=execution_id,
        )

    assert result is existing
    recover.assert_awaited_once()
    assert recover.await_args.kwargs["execution_id"] == execution_id
    assert recover.await_args.kwargs["workflow_id"] == workflow.id
    assert recover.await_args.kwargs["parameters"] == {"ring": 3}
    run_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_workflow_propagates_new_submission_identity() -> None:
    user = _exec_user()
    workflow = _workflow()
    repo = _WorkflowRepo(workflow=workflow)
    execution_id = uuid4()
    service_result = WorkflowExecutionResponse(
        execution_id=str(execution_id),
        workflow_id=str(workflow.id),
        workflow_name=workflow.name,
        status=ExecutionStatus.PENDING,
    )

    with (
        patch("src.repositories.WorkflowRepository", return_value=repo),
        patch(
            "src.services.execution.submission_recovery.recover_execution_submission",
            AsyncMock(return_value=None),
        ),
        patch(
            "src.services.execution.service.run_workflow",
            AsyncMock(return_value=service_result),
        ) as run_workflow,
        patch.object(workflows, "publish_execution_update", AsyncMock()),
        patch.object(workflows, "publish_history_update", AsyncMock()),
    ):
        result = await workflows.execute_workflow(
            WorkflowExecutionRequest(workflow_id="sync_records"),
            _ctx(user),
            _Db(),
            user,
            execution_request_id=execution_id,
        )

    assert result.execution_id == str(execution_id)
    run_workflow.assert_awaited_once()
    assert run_workflow.await_args.kwargs["context"].execution_id == str(execution_id)


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
async def test_execute_workflow_runs_data_provider_without_cache() -> None:
    user = _exec_user()
    workflow = _workflow(type="data_provider", cache_ttl_seconds=0)
    repo = _WorkflowRepo(workflow=workflow)
    service_result = WorkflowExecutionResponse(
        execution_id=str(uuid4()),
        workflow_id=str(workflow.id),
        workflow_name=workflow.name,
        status=ExecutionStatus.SUCCESS,
        result={"rows": [1]},
    )

    with (
        patch("src.repositories.WorkflowRepository", return_value=repo),
        patch("src.services.execution.service.run_workflow", AsyncMock(return_value=service_result)) as run_workflow,
    ):
        result = await workflows.execute_workflow(
            WorkflowExecutionRequest(
                workflow_id="options",
                input_data={"q": "o"},
                transient=False,
            ),
            _ctx(user),
            _Db(),
            user,
        )

    assert result.status == ExecutionStatus.SUCCESS
    assert result.result == {"rows": [1]}
    assert result.is_transient is False
    run_workflow.assert_awaited_once()
    assert run_workflow.await_args.kwargs["workflow_id"] == str(workflow.id)
    assert run_workflow.await_args.kwargs["sync"] is True
    assert run_workflow.await_args.kwargs["transient"] is False


@pytest.mark.asyncio
async def test_execute_workflow_runs_as_user_and_publishes_terminal_result() -> None:
    admin = _exec_user(is_superuser=True)
    run_as = SimpleNamespace(
        id=uuid4(),
        name=None,
        email="delegate@example.com",
        is_superuser=False,
        is_external=False,
        organization_id=None,
    )
    workflow = _workflow(type="workflow", organization_id=None)
    repo = _WorkflowRepo(workflow=workflow)
    service_result = WorkflowExecutionResponse(
        execution_id=str(uuid4()),
        workflow_id=str(workflow.id),
        workflow_name=workflow.name,
        status=ExecutionStatus.SUCCESS,
        result={"ok": True},
        duration_ms=12,
    )

    with (
        patch("src.repositories.WorkflowRepository", return_value=repo),
        patch("src.services.execution.service.run_workflow", AsyncMock(return_value=service_result)) as run_workflow,
        patch.object(workflows, "publish_execution_update", AsyncMock()) as publish_execution_update,
        patch.object(workflows, "publish_history_update", AsyncMock()) as publish_history_update,
    ):
        result = await workflows.execute_workflow(
            WorkflowExecutionRequest(
                workflow_id="sync_records",
                input_data={"ticket": "123"},
                sync=True,
                run_as=str(run_as.id),
            ),
            _ctx(admin),
            _Db(run_as),
            admin,
        )

    assert result is service_result
    assert result.is_transient is True
    run_workflow.assert_awaited_once()
    shared_ctx = run_workflow.await_args.kwargs["context"]
    assert shared_ctx.user_id == str(run_as.id)
    assert shared_ctx.email == "delegate@example.com"
    assert shared_ctx.is_platform_admin is False
    assert run_workflow.await_args.kwargs["input_data"] == {"ticket": "123"}
    assert run_workflow.await_args.kwargs["sync"] is True
    publish_execution_update.assert_awaited_once()
    publish_history_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_workflow_runs_inline_code_for_admin_without_publish_when_transient() -> None:
    admin = _exec_user(is_superuser=True)
    service_result = WorkflowExecutionResponse(
        execution_id=str(uuid4()),
        workflow_name="inline.py",
        status=ExecutionStatus.SUCCESS,
        result={"ran": True},
    )

    with (
        patch("src.repositories.WorkflowRepository", return_value=_WorkflowRepo()),
        patch("src.services.execution.service.run_code", AsyncMock(return_value=service_result)) as run_code,
        patch.object(workflows, "publish_execution_update", AsyncMock()) as publish_execution_update,
        patch.object(workflows, "publish_history_update", AsyncMock()) as publish_history_update,
    ):
        result = await workflows.execute_workflow(
            WorkflowExecutionRequest(
                code="cHJpbnQoJ2hpJyk=",
                script_name="inline.py",
                input_data={"x": 1},
                transient=True,
            ),
            _ctx(admin),
            _Db(),
            admin,
        )

    assert result is service_result
    assert result.is_transient is True
    run_code.assert_awaited_once()
    assert run_code.await_args.kwargs["script_name"] == "inline.py"
    assert run_code.await_args.kwargs["input_data"] == {"x": 1}
    assert run_code.await_args.kwargs["transient"] is True
    publish_execution_update.assert_not_awaited()
    publish_history_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_workflow_rejects_org_and_run_as_overrides_for_regular_user() -> None:
    user = _exec_user(is_superuser=False)
    workflow = _workflow()
    repo = _WorkflowRepo(workflow=workflow)

    for request in (
        WorkflowExecutionRequest(workflow_id="sync_records", org_id=str(uuid4())),
        WorkflowExecutionRequest(workflow_id="sync_records", run_as=str(uuid4())),
    ):
        with patch("src.repositories.WorkflowRepository", return_value=repo):
            with pytest.raises(HTTPException) as exc:
                await workflows.execute_workflow(request, _ctx(user), _Db(), user)

        assert exc.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc.value.detail == "org_id and run_as overrides require platform admin"


@pytest.mark.asyncio
async def test_execute_workflow_reports_missing_run_as_user() -> None:
    admin = _exec_user(is_superuser=True)
    workflow = _workflow()
    repo = _WorkflowRepo(workflow=workflow)
    missing_user_id = uuid4()

    with patch("src.repositories.WorkflowRepository", return_value=repo):
        with pytest.raises(HTTPException) as exc:
            await workflows.execute_workflow(
                WorkflowExecutionRequest(
                    workflow_id="sync_records",
                    run_as=str(missing_user_id),
                ),
                _ctx(admin),
                _Db(None),
                admin,
            )

    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc.value.detail == f"run_as user '{missing_user_id}' not found"


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
