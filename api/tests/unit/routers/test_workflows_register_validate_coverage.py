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


def _admin(**overrides):
    data = {
        "email": "admin@example.com",
        "organization_id": uuid4(),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


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
