from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.enums import ExecutionStatus
from src.routers import executions


def _ctx(*, user=None):
    return SimpleNamespace(db=object(), user=user or SimpleNamespace(is_superuser=True, user_id=uuid4()))


def _execution_row(**overrides):
    row = SimpleNamespace(
        id=uuid4(),
        workflow_name="Sync Tickets",
        workflow_id=uuid4(),
        organization_id=None,
        organization=None,
        form_id=None,
        executed_by=uuid4(),
        executed_by_name=None,
        executed_by_user=None,
        status=ExecutionStatus.RUNNING.value,
        parameters={"ticket": "T1"},
        result={"ok": True},
        result_type="json",
        error_message=None,
        duration_ms=42,
        started_at=datetime.now(UTC),
        completed_at=None,
        scheduled_at=None,
        variables={"secret": "visible-to-admin"},
        execution_context={"trace": "admin-only"},
        session_id=uuid4(),
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def test_to_pydantic_hides_admin_only_fields_for_regular_user():
    repo = executions.ExecutionRepository.__new__(executions.ExecutionRepository)
    user = SimpleNamespace(is_superuser=False)
    org_id = uuid4()
    row = _execution_row(
        organization_id=org_id,
        organization=SimpleNamespace(name="Contoso"),
        executed_by_user=SimpleNamespace(email="owner@example.com"),
    )

    result = repo._to_pydantic(row, user)

    assert result.execution_id == str(row.id)
    assert result.org_id == str(org_id)
    assert result.org_name == "Contoso"
    assert result.executed_by_email == "owner@example.com"
    assert result.variables is None
    assert result.execution_context is None


def test_to_pydantic_includes_admin_only_fields_for_superuser_and_global_name():
    repo = executions.ExecutionRepository.__new__(executions.ExecutionRepository)
    row = _execution_row(organization_id=None)

    result = repo._to_pydantic(row, SimpleNamespace(is_superuser=True))

    assert result.org_name == "Global"
    assert result.variables == {"secret": "visible-to-admin"}
    assert result.execution_context == {"trace": "admin-only"}


@pytest.mark.asyncio
async def test_list_logs_parses_filters_and_invalid_continuation_defaults_to_zero():
    logs = [
        {
            "id": 1,
            "execution_id": str(uuid4()),
            "workflow_name": "Sync",
            "organization_id": uuid4(),
            "organization_name": "Contoso",
            "timestamp": datetime.now(UTC),
            "level": "ERROR",
            "message": "failed",
        }
    ]

    with patch.object(executions, "ExecutionLogRepository") as repo_cls:
        repo = repo_cls.return_value
        repo.list_logs = AsyncMock(return_value=(logs, "50"))

        response = await executions.list_logs(
            _ctx(),
            organization_id=logs[0]["organization_id"],
            workflow_name="Sync",
            levels="error, warning",
            message_search="fail",
            start_date="2026-07-05T10:00:00Z",
            end_date="2026-07-05T11:00:00Z",
            limit=25,
            continuation_token="not-an-int",
        )

    assert response.continuation_token == "50"
    assert response.logs[0].message == "failed"
    assert repo.list_logs.await_args.kwargs["levels"] == ["ERROR", "WARNING"]
    assert repo.list_logs.await_args.kwargs["offset"] == 0
    assert repo.list_logs.await_args.kwargs["start_date"].tzinfo is None


@pytest.mark.asyncio
async def test_get_execution_endpoint_maps_repository_errors():
    execution_id = uuid4()

    with patch.object(executions, "ExecutionRepository") as repo_cls:
        repo_cls.return_value.get_execution = AsyncMock(return_value=(None, "Forbidden"))
        with pytest.raises(HTTPException) as exc:
            await executions.get_execution(execution_id, _ctx())
    assert exc.value.status_code == 403

    with patch.object(executions, "ExecutionRepository") as repo_cls:
        repo_cls.return_value.get_execution = AsyncMock(return_value=(None, "NotFound"))
        with pytest.raises(HTTPException) as exc:
            await executions.get_execution(execution_id, _ctx())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_cancel_execution_sets_cancel_flag_for_running_execution():
    execution_id = uuid4()
    running = SimpleNamespace(status=ExecutionStatus.CANCELLING)
    redis_client = SimpleNamespace(
        set_cancel_flag=AsyncMock(),
        publish_cancel_event=AsyncMock(),
        set_pending_cancelled=AsyncMock(),
    )

    with (
        patch.object(executions, "get_redis_client", return_value=redis_client),
        patch.object(executions, "ExecutionRepository") as repo_cls,
    ):
        repo_cls.return_value.cancel_execution = AsyncMock(return_value=(running, None))
        result = await executions.cancel_execution(execution_id, _ctx())

    assert result is running
    redis_client.set_cancel_flag.assert_awaited_once_with(str(execution_id))
    redis_client.publish_cancel_event.assert_awaited_once_with(str(execution_id))
    redis_client.set_pending_cancelled.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_execution_handles_redis_pending_and_not_found():
    execution_id = uuid4()
    redis_client = SimpleNamespace(
        set_cancel_flag=AsyncMock(),
        publish_cancel_event=AsyncMock(),
        set_pending_cancelled=AsyncMock(return_value=True),
    )

    with (
        patch.object(executions, "get_redis_client", return_value=redis_client),
        patch.object(executions, "ExecutionRepository") as repo_cls,
    ):
        repo_cls.return_value.cancel_execution = AsyncMock(return_value=(None, "NotFound"))
        result = await executions.cancel_execution(execution_id, _ctx())

    assert result["status"] == "Cancelled"
    redis_client.set_pending_cancelled.assert_awaited_once_with(str(execution_id))

    redis_client.set_pending_cancelled = AsyncMock(return_value=False)
    with (
        patch.object(executions, "get_redis_client", return_value=redis_client),
        patch.object(executions, "ExecutionRepository") as repo_cls,
    ):
        repo_cls.return_value.cancel_execution = AsyncMock(return_value=(None, "NotFound"))
        with pytest.raises(HTTPException) as exc:
            await executions.cancel_execution(execution_id, _ctx())

    assert exc.value.status_code == 404
