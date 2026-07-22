from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.enums import ExecutionStatus
from src.core.org_filter import OrgFilterType
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


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _DbResult:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def one_or_none(self):
        return self._row

    def scalars(self):
        return _ScalarResult(self._rows)


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
async def test_list_executions_resolves_org_scope_and_parses_filters():
    org_id = uuid4()
    workflow_id = uuid4()
    user = SimpleNamespace(is_superuser=True, user_id=uuid4())
    expected_execution = executions.ExecutionRepository.__new__(
        executions.ExecutionRepository
    )._to_pydantic(_execution_row(), user)

    with (
        patch.object(
            executions,
            "resolve_org_filter",
            return_value=(OrgFilterType.ORG_PLUS_GLOBAL, org_id),
        ) as resolve_org_filter,
        patch.object(executions, "ExecutionRepository") as repo_cls,
    ):
        repo_cls.return_value.list_executions = AsyncMock(
            return_value=([expected_execution], "75")
        )

        response = await executions.list_executions(
            _ctx(user=user),
            scope=str(org_id),
            workflowName="Sync Tickets",
            workflowId=str(workflow_id),
            status_filter="Failed,Timeout",
            startDate="2026-07-05T10:00:00Z",
            endDate="2026-07-05T11:00:00Z",
            excludeLocal=False,
            limit=25,
            continuationToken="50",
        )

    assert response.executions == [expected_execution]
    assert response.continuation_token == "75"
    resolve_org_filter.assert_called_once_with(user, str(org_id))
    assert repo_cls.return_value.list_executions.await_args.kwargs == {
        "user": user,
        "org_id": org_id,
        "workflow_name": "Sync Tickets",
        "workflow_id": workflow_id,
        "status_filter": "Failed,Timeout",
        "start_date": "2026-07-05T10:00:00Z",
        "end_date": "2026-07-05T11:00:00Z",
        "exclude_local": False,
        "limit": 25,
        "offset": 50,
        "cursor": None,
    }


@pytest.mark.asyncio
async def test_list_executions_maps_invalid_scope_to_422():
    with patch.object(
        executions,
        "resolve_org_filter",
        side_effect=ValueError("invalid scope"),
    ):
        with pytest.raises(HTTPException) as exc:
            await executions.list_executions(_ctx(), scope="not-a-scope")

    assert exc.value.status_code == 422
    assert exc.value.detail == "invalid scope"


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
async def test_result_logs_and_variables_endpoints_map_repository_errors():
    execution_id = uuid4()

    with patch.object(executions, "ExecutionRepository") as repo_cls:
        repo_cls.return_value.get_execution_result = AsyncMock(
            return_value=(None, "Forbidden")
        )
        with pytest.raises(HTTPException) as exc:
            await executions.get_execution_result(execution_id, _ctx())
    assert exc.value.status_code == 403

    with patch.object(executions, "ExecutionRepository") as repo_cls:
        repo_cls.return_value.get_execution_logs = AsyncMock(return_value=(None, "NotFound"))
        with pytest.raises(HTTPException) as exc:
            await executions.get_execution_logs(execution_id, _ctx())
    assert exc.value.status_code == 404

    with patch.object(executions, "ExecutionRepository") as repo_cls:
        repo_cls.return_value.get_execution_variables = AsyncMock(
            return_value=(None, "Forbidden")
        )
        with pytest.raises(HTTPException) as exc:
            await executions.get_execution_variables(execution_id, _ctx())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_execution_result_authorizes_owner_and_superuser():
    execution_id = uuid4()
    owner_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=_DbResult(
                row=SimpleNamespace(
                    result={"ok": True},
                    result_type="json",
                    executed_by=owner_id,
                )
            )
        )
    )
    repo = executions.ExecutionRepository(db)

    owner_result, owner_error = await repo.get_execution_result(
        execution_id,
        SimpleNamespace(is_superuser=False, user_id=owner_id),
    )
    super_result, super_error = await repo.get_execution_result(
        execution_id,
        SimpleNamespace(is_superuser=True, user_id=uuid4()),
    )

    assert owner_error is None
    assert owner_result == {"result": {"ok": True}, "result_type": "json"}
    assert super_error is None
    assert super_result == {"result": {"ok": True}, "result_type": "json"}


@pytest.mark.asyncio
async def test_get_execution_result_returns_forbidden_and_not_found():
    execution_id = uuid4()
    owner_id = uuid4()
    repo = executions.ExecutionRepository(
        SimpleNamespace(
            execute=AsyncMock(return_value=_DbResult(row=SimpleNamespace(executed_by=owner_id)))
        )
    )

    result, error = await repo.get_execution_result(
        execution_id,
        SimpleNamespace(is_superuser=False, user_id=uuid4()),
    )
    assert result is None
    assert error == "Forbidden"

    repo = executions.ExecutionRepository(
        SimpleNamespace(execute=AsyncMock(return_value=_DbResult(row=None)))
    )
    result, error = await repo.get_execution_result(
        execution_id,
        SimpleNamespace(is_superuser=True, user_id=uuid4()),
    )
    assert result is None
    assert error == "NotFound"


@pytest.mark.asyncio
async def test_get_execution_variables_forbidden_not_found_and_empty_defaults():
    execution_id = uuid4()
    repo = executions.ExecutionRepository(SimpleNamespace(execute=AsyncMock()))

    variables, error = await repo.get_execution_variables(
        execution_id,
        SimpleNamespace(is_superuser=False, user_id=uuid4()),
    )
    assert variables is None
    assert error == "Forbidden"
    repo.db.execute.assert_not_called()

    repo = executions.ExecutionRepository(
        SimpleNamespace(execute=AsyncMock(return_value=_DbResult(row=None)))
    )
    variables, error = await repo.get_execution_variables(
        execution_id,
        SimpleNamespace(is_superuser=True, user_id=uuid4()),
    )
    assert variables is None
    assert error == "NotFound"

    repo = executions.ExecutionRepository(
        SimpleNamespace(execute=AsyncMock(return_value=_DbResult(row=(execution_id, None))))
    )
    variables, error = await repo.get_execution_variables(
        execution_id,
        SimpleNamespace(is_superuser=True, user_id=uuid4()),
    )
    assert variables == {}
    assert error is None


@pytest.mark.asyncio
async def test_get_execution_logs_reads_redis_and_filters_hidden_levels():
    execution_id = uuid4()
    owner_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=_DbResult(
                row=SimpleNamespace(
                    executed_by=owner_id,
                    status=ExecutionStatus.RUNNING,
                )
            )
        )
    )
    repo = executions.ExecutionRepository(db)
    stream_logs = [
        SimpleNamespace(
            level="INFO",
            timestamp="2026-07-05T10:00:00Z",
            message="shown",
            metadata={"step": 1},
        ),
        SimpleNamespace(
            level="DEBUG",
            timestamp="2026-07-05T10:00:01Z",
            message="hidden",
            metadata={"step": 2},
        ),
    ]

    with patch.object(executions, "read_logs_from_stream", AsyncMock(return_value=stream_logs)):
        logs, error = await repo.get_execution_logs(
            execution_id,
            SimpleNamespace(is_superuser=False, user_id=owner_id),
        )

    assert error is None
    assert logs is not None
    assert [log.message for log in logs] == ["shown"]
    assert logs[0].level == "info"
    assert logs[0].data == {"step": 1}


@pytest.mark.asyncio
async def test_get_execution_logs_falls_back_to_db_when_redis_read_fails():
    execution_id = uuid4()
    owner_id = uuid4()
    db_log = SimpleNamespace(
        id=7,
        timestamp=datetime(2026, 7, 5, 10, 0, tzinfo=UTC),
        level="ERROR",
        message="from db",
        log_metadata={"source": "postgres"},
        sequence=3,
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _DbResult(
                    row=SimpleNamespace(
                        executed_by=owner_id,
                        status=ExecutionStatus.PENDING,
                    )
                ),
                _DbResult(rows=[db_log]),
            ]
        )
    )
    repo = executions.ExecutionRepository(db)

    with patch.object(
        executions,
        "read_logs_from_stream",
        AsyncMock(side_effect=RuntimeError("redis unavailable")),
    ):
        logs, error = await repo.get_execution_logs(
            execution_id,
            SimpleNamespace(is_superuser=True, user_id=uuid4()),
        )

    assert error is None
    assert len(logs) == 1
    assert logs[0].message == "from db"
    assert logs[0].data == {"source": "postgres"}
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_cancel_execution_maps_forbidden_and_bad_request_without_redis_mutation():
    execution_id = uuid4()
    redis_client = SimpleNamespace(
        set_cancel_flag=AsyncMock(),
        publish_cancel_event=AsyncMock(),
        set_pending_cancelled=AsyncMock(),
    )

    for error, status_code in (("Forbidden", 403), ("BadRequest", 400)):
        with (
            patch.object(executions, "get_redis_client", return_value=redis_client),
            patch.object(executions, "ExecutionRepository") as repo_cls,
        ):
            repo_cls.return_value.cancel_execution = AsyncMock(return_value=(None, error))
            with pytest.raises(HTTPException) as exc:
                await executions.cancel_execution(execution_id, _ctx())

        assert exc.value.status_code == status_code
        redis_client.set_cancel_flag.assert_not_called()
        redis_client.publish_cancel_event.assert_not_called()
        redis_client.set_pending_cancelled.assert_not_called()


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
        result = cast(dict[str, Any], await executions.cancel_execution(execution_id, _ctx()))

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
        result = cast(dict[str, Any], await executions.cancel_execution(execution_id, _ctx()))

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
