from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
from src.repositories import executions
from src.repositories.executions import ExecutionRepository, _make_json_safe


def _user(*, is_superuser: bool = False) -> UserPrincipal:
    user_id = uuid4()
    return UserPrincipal(
        user_id=user_id,
        email=f"{user_id}@example.test",
        organization_id=uuid4(),
        is_superuser=is_superuser,
    )


def _execution(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": uuid4(),
        "workflow_name": "sync_ticket",
        "workflow_id": uuid4(),
        "organization_id": uuid4(),
        "form_id": uuid4(),
        "executed_by": uuid4(),
        "executed_by_name": "Runner",
        "status": ExecutionStatus.SUCCESS.value,
        "parameters": {"ticket_id": 123},
        "result": {"ok": True},
        "result_type": "json",
        "error_message": None,
        "duration_ms": 250,
        "created_at": now,
        "started_at": now,
        "completed_at": now,
        "variables": {"secret": "hidden"},
        "execution_context": None,
        "peak_memory_bytes": None,
        "cpu_total_seconds": None,
        "session_id": uuid4(),
        "time_saved": 7,
        "value": Decimal("12.50"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_make_json_safe_converts_nested_non_json_values() -> None:
    now = datetime(2026, 7, 4, tzinfo=timezone.utc)
    identifier = uuid4()

    safe = _make_json_safe(
        {
            "when": now,
            "id": identifier,
            "cost": Decimal("1.25"),
            "items": [Decimal("2.50"), None],
        }
    )

    assert safe == {
        "when": "2026-07-04 00:00:00+00:00",
        "id": str(identifier),
        "cost": "1.25",
        "items": ["2.50", None],
    }


def test_make_json_safe_preserves_none() -> None:
    assert _make_json_safe(None) is None


def test_to_pydantic_hides_admin_fields_for_regular_user() -> None:
    repo = ExecutionRepository(session=None)  # type: ignore[arg-type]
    execution = _execution(executed_by_name=None, parameters=None, time_saved=None, value=None)

    result = repo._to_pydantic(execution, _user())

    assert result.execution_id == str(execution.id)
    assert result.workflow_name == "sync_ticket"
    assert result.workflow_id == str(execution.workflow_id)
    assert result.org_id == str(execution.organization_id)
    assert result.form_id == str(execution.form_id)
    assert result.executed_by == str(execution.executed_by)
    assert result.executed_by_name == str(execution.executed_by)
    assert result.status == ExecutionStatus.SUCCESS
    assert result.input_data == {}
    assert result.logs is None
    assert result.variables is None
    assert result.time_saved == 0
    assert result.value == 0


def test_to_pydantic_includes_admin_fields_for_superuser() -> None:
    repo = ExecutionRepository(session=None)  # type: ignore[arg-type]
    execution = _execution()

    result = repo._to_pydantic(execution, _user(is_superuser=True))

    assert result.variables == {"secret": "hidden"}
    assert result.session_id == str(execution.session_id)
    assert result.time_saved == 7
    assert result.value == 12.5


def test_to_pydantic_without_user_treats_request_as_non_admin() -> None:
    repo = ExecutionRepository(session=None)  # type: ignore[arg-type]
    execution = _execution(workflow_id=None, organization_id=None, form_id=None, session_id=None)

    result = repo._to_pydantic(execution)

    assert result.workflow_id is None
    assert result.org_id is None
    assert result.form_id is None
    assert result.session_id is None
    assert result.variables is None


@pytest.mark.asyncio
async def test_create_execution_updates_existing_scheduled_row() -> None:
    execution_id = uuid4()
    existing = _execution(id=execution_id, status=ExecutionStatus.SCHEDULED.value)
    session = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock(return_value=existing)
    repo = ExecutionRepository(session)

    result = await repo.create_execution(
        execution_id=str(execution_id),
        workflow_name="scheduled_sync",
        parameters={"ticket": 1},
        org_id=f"ORG:{uuid4()}",
        user_id=str(uuid4()),
        user_name="Scheduler",
        form_id=str(uuid4()),
        api_key_id=str(uuid4()),
        status=ExecutionStatus.RUNNING,
        is_local_execution=True,
        execution_model="process",
        workflow_id=str(uuid4()),
    )

    assert result is existing
    assert existing.workflow_name == "scheduled_sync"
    assert existing.status == ExecutionStatus.RUNNING
    assert existing.is_local_execution is True
    session.add.assert_not_called()
    session.flush.assert_awaited_once()
    session.refresh.assert_awaited_once_with(existing)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cancel_status",
    [ExecutionStatus.CANCELLING, ExecutionStatus.CANCELLED],
)
async def test_create_execution_does_not_resurrect_cancelled_claim(
    cancel_status: ExecutionStatus,
) -> None:
    execution_id = uuid4()
    existing = _execution(id=execution_id, status=cancel_status.value)
    session = AsyncMock()
    session.get.return_value = existing
    repo = ExecutionRepository(session)

    await repo.create_execution(
        execution_id=str(execution_id),
        workflow_name="cancelled_setup",
        parameters={},
        org_id=None,
        user_id=str(uuid4()),
        user_name="Runner",
        status=ExecutionStatus.RUNNING,
    )

    assert existing.status == cancel_status


@pytest.mark.asyncio
async def test_create_execution_adds_new_global_execution() -> None:
    execution_id = uuid4()
    user_id = uuid4()
    session = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock(return_value=None)
    repo = ExecutionRepository(session)

    result = await repo.create_execution(
        execution_id=str(execution_id),
        workflow_name="manual_sync",
        parameters={"ok": True},
        org_id="GLOBAL",
        user_id=str(user_id),
        user_name="Runner",
    )

    assert result.id == execution_id
    assert result.organization_id is None
    assert result.executed_by == user_id
    session.add.assert_called_once_with(result)
    session.flush.assert_awaited_once()
    session.refresh.assert_awaited_once_with(result)


@pytest.mark.asyncio
async def test_create_execution_redacts_sensitive_endpoint_inputs() -> None:
    execution_id = uuid4()
    session = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock(return_value=None)
    repo = ExecutionRepository(session)

    result = await repo.create_execution(
        execution_id=str(execution_id),
        workflow_name="promote_source",
        parameters={
            "mode": "plan",
            "github_oidc_token": "SYNTHETIC_OIDC_TOKEN",
            "source_code": "SYNTHETIC_SOURCE_ARCHIVE",
        },
        org_id="GLOBAL",
        user_id=str(uuid4()),
        user_name="API Key",
    )

    assert result.parameters == {
        "mode": "plan",
        "github_oidc_token": "[REDACTED]",
        "source_code": "[REDACTED]",
    }
    assert "SYNTHETIC_OIDC_TOKEN" not in json.dumps(result.parameters)
    assert "SYNTHETIC_SOURCE_ARCHIVE" not in json.dumps(result.parameters)


@pytest.mark.asyncio
async def test_update_execution_sets_result_type_metrics_economics_and_logs() -> None:
    execution_id = uuid4()
    session = AsyncMock()
    session.add = MagicMock()
    session.execute.side_effect = [
        None,
        ExecuteResult(scalar_row=ExecutionStatus.RUNNING.value),
        None,
    ]
    repo = ExecutionRepository(session)

    await repo.update_execution(
        execution_id=str(execution_id),
        status=ExecutionStatus.SUCCESS,
        result="<p>done</p>",
        error_message="ignored by clients for success",
        duration_ms=42,
        logs=[
            {
                "timestamp": "2026-07-04T12:00:00+00:00",
                "level": "warning",
                "message": "careful",
                "data": {"step": 1},
            },
            {"level": "info", "message": "done"},
        ],
        variables={"when": datetime(2026, 7, 4, tzinfo=timezone.utc)},
        execution_context={"run": uuid4()},
        metrics={
            "peak_memory_bytes": 100,
            "process_rss_bytes": 80,
            "cpu_user_seconds": 1.2,
            "cpu_system_seconds": 0.3,
            "cpu_total_seconds": 1.5,
        },
        time_saved=5,
        value=12.25,
    )

    statement = session.execute.await_args_list[-1].args[0]
    values = statement.compile().params
    assert values["status"] == ExecutionStatus.SUCCESS.value
    assert values["result"] == "<p>done</p>"
    assert values["result_type"] == "html"
    assert values["duration_ms"] == 42
    assert values["peak_memory_bytes"] == 100
    assert values["process_rss_bytes"] == 80
    assert values["cpu_total_seconds"] == 1.5
    assert values["time_saved"] == 5
    assert values["value"] == 12.25
    assert session.add.call_count == 2
    first_log = session.add.call_args_list[0].args[0]
    second_log = session.add.call_args_list[1].args[0]
    assert first_log.level == "WARNING"
    assert first_log.log_metadata == {"step": 1}
    assert second_log.sequence == 1
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_execution_redacts_generated_script_before_persistence() -> None:
    marker = "SYNTHETIC_EXECUTION_SCRIPT_MARKER"
    session = AsyncMock()
    session.add = MagicMock()
    session.execute.side_effect = [
        None,
        ExecuteResult(scalar_row=ExecutionStatus.RUNNING.value),
        None,
    ]
    repo = ExecutionRepository(session)

    await repo.update_execution(
        execution_id=str(uuid4()),
        status=ExecutionStatus.SUCCESS,
        variables={
            "script": marker,
            "nested": {
                "credential": "SYNTHETIC_CREDENTIAL_MARKER",
                "status": "safe",
            },
        },
    )

    statement = session.execute.await_args_list[-1].args[0]
    persisted = statement.compile().params["variables"]
    assert persisted == {
        "script": "[REDACTED]",
        "nested": {"credential": "[REDACTED]", "status": "safe"},
    }
    serialized = json.dumps(persisted)
    assert marker not in serialized
    assert "SYNTHETIC_CREDENTIAL_MARKER" not in serialized

    raw_admin = repo._to_pydantic(
        _execution(variables=persisted),
        _user(is_superuser=True),
    )
    admin_serialized = json.dumps(raw_admin.variables)
    assert raw_admin.variables == persisted
    assert marker not in admin_serialized
    assert "SYNTHETIC_CREDENTIAL_MARKER" not in admin_serialized


@pytest.mark.asyncio
async def test_update_execution_classifies_json_text_and_default_result_types() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    session.execute.side_effect = [
        item
        for _ in range(4)
        for item in (
            None,
            ExecuteResult(scalar_row=ExecutionStatus.RUNNING.value),
            None,
        )
    ]
    repo = ExecutionRepository(session)

    for result, expected in [
        ({"ok": True}, "json"),
        (["a"], "json"),
        ("plain text", "text"),
        (123, "json"),
    ]:
        await repo.update_execution(str(uuid4()), ExecutionStatus.SUCCESS, result=result)
        statement = session.execute.await_args_list[-1].args[0]
        assert statement.compile().params["result_type"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current_status",
    [ExecutionStatus.CANCELLING, ExecutionStatus.CANCELLED],
)
async def test_update_execution_preserves_accepted_cancellation(
    current_status: ExecutionStatus,
) -> None:
    execution_id = uuid4()
    session = AsyncMock()
    session.add = MagicMock()
    session.execute.side_effect = [
        None,
        ExecuteResult(scalar_row=current_status.value),
        None,
    ]
    repo = ExecutionRepository(session)

    effective_status = await repo.update_execution(
        str(execution_id),
        ExecutionStatus.SUCCESS,
        result={"completed": True},
        error_message="late worker error",
        time_saved=4,
        value=12.5,
    )

    advisory_lock = session.execute.await_args_list[0]
    assert advisory_lock.args[1] == {"execution_id": str(execution_id)}
    statement = session.execute.await_args_list[-1].args[0]
    values = statement.compile().params
    assert values["status"] == ExecutionStatus.CANCELLED.value
    assert "result" not in values
    assert "error_message" not in values
    assert "time_saved" not in values
    assert "value" not in values
    assert effective_status == ExecutionStatus.CANCELLED


class OneOrNoneResult:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row


class ScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class ExecuteResult:
    def __init__(self, *, scalar=None, scalar_row=None, scalars=None, one=None):
        self._scalar = scalar
        self._scalar_row = scalar_row
        self._scalars = scalars if scalars is not None else []
        self._one = one

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar_row

    def scalars(self):
        return ScalarResult(self._scalars)

    def one(self):
        return self._one

    def one_or_none(self):
        return self._one


@pytest.mark.asyncio
async def test_list_executions_paginates_and_maps_rows() -> None:
    user = _user(is_superuser=True)
    first = _execution(executed_by=user.user_id, workflow_name="sync_ticket")
    second = _execution(executed_by=user.user_id, workflow_name="sync_ticket")
    extra = _execution(executed_by=user.user_id, workflow_name="sync_ticket")
    session = AsyncMock()
    session.execute = AsyncMock(return_value=ExecuteResult(scalars=[first, second, extra]))
    repo = ExecutionRepository(session)

    rows, next_token = await repo.list_executions(
        user,
        org_id=first.organization_id,
        workflow_name="sync_ticket",
        status_filter=ExecutionStatus.SUCCESS.value,
        start_date="not-a-date",
        end_date="also-not-a-date",
        limit=2,
        offset=10,
    )

    assert [row.execution_id for row in rows] == [str(first.id), str(second.id)]
    assert next_token == "12"
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_list_executions_scopes_regular_users_to_their_own_rows() -> None:
    user = _user()
    owned = _execution(executed_by=user.user_id, variables={"hidden": True})
    session = AsyncMock()
    session.execute = AsyncMock(return_value=ExecuteResult(scalars=[owned]))
    repo = ExecutionRepository(session)

    rows, next_token = await repo.list_executions(
        user,
        org_id=None,
        start_date="2026-07-04T00:00:00Z",
        end_date="2026-07-05T00:00:00Z",
    )

    assert next_token is None
    assert len(rows) == 1
    assert rows[0].executed_by == str(user.user_id)
    assert rows[0].variables is None


@pytest.mark.asyncio
async def test_get_execution_returns_not_found_and_forbidden() -> None:
    owner = _user()
    other = _user()
    execution = _execution(executed_by=owner.user_id)
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            ExecuteResult(scalar_row=None),
            ExecuteResult(scalar_row=execution),
        ]
    )
    repo = ExecutionRepository(session)

    result, error = await repo.get_execution(uuid4(), owner)
    assert result is None
    assert error == "NotFound"

    result, error = await repo.get_execution(execution.id, other)
    assert result is None
    assert error == "Forbidden"


@pytest.mark.asyncio
async def test_get_execution_includes_logs_ai_usage_totals_and_admin_fields() -> None:
    admin = _user(is_superuser=True)
    execution = _execution(executed_by=admin.user_id)
    log = SimpleNamespace(
        id=1,
        timestamp=datetime(2026, 7, 4, 12, tzinfo=timezone.utc),
        level="DEBUG",
        message="debug detail",
        log_metadata={"step": 1},
        sequence=0,
    )
    ai_usage = SimpleNamespace(
        provider="openai",
        model="gpt-test",
        input_tokens=11,
        output_tokens=7,
        cache_read_tokens=3,
        cache_write_tokens=2,
        provider_cost=Decimal("0.1000"),
        cost=Decimal("0.1234"),
        duration_ms=250,
        timestamp=datetime(2026, 7, 4, 12, 1, tzinfo=timezone.utc),
        sequence=2,
    )
    totals = SimpleNamespace(
        total_input=11,
        total_output=7,
        total_cache_read=3,
        total_cache_write=2,
        total_provider_cost=Decimal("0.1000"),
        total_cost=Decimal("0.1234"),
        total_duration=250,
        call_count=1,
    )
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            ExecuteResult(scalar_row=execution),
            ExecuteResult(scalars=[log]),
            ExecuteResult(scalars=[ai_usage]),
            ExecuteResult(one=totals),
        ]
    )
    repo = ExecutionRepository(session)

    result, error = await repo.get_execution(execution.id, admin)

    assert error is None
    assert result is not None
    assert result.logs == [
        {
            "id": log.id,
            "timestamp": "2026-07-04T12:00:00+00:00",
            "level": "DEBUG",
            "message": "debug detail",
            "data": {"step": 1},
            "sequence": 0,
        }
    ]
    assert result.variables == {"secret": "hidden"}
    assert result.execution_context is None
    assert result.peak_memory_bytes is None
    assert result.ai_usage is not None
    assert result.ai_usage[0].provider == "openai"
    assert result.ai_usage[0].cost == "0.1234"
    assert result.ai_totals is not None
    assert result.ai_totals.total_input_tokens == 11
    assert result.ai_totals.total_cost == "0.1234"


@pytest.mark.asyncio
async def test_get_execution_logs_handles_access_and_filters_to_public_logs() -> None:
    owner = _user()
    other = _user()
    log = SimpleNamespace(
        id=2,
        timestamp=None,
        level=None,
        message=None,
        log_metadata={"visible": True},
        sequence=None,
    )
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            OneOrNoneResult(None),
            OneOrNoneResult(SimpleNamespace(executed_by=owner.user_id)),
            OneOrNoneResult(SimpleNamespace(executed_by=owner.user_id)),
            ExecuteResult(scalars=[log]),
        ]
    )
    repo = ExecutionRepository(session)

    logs, error = await repo.get_execution_logs(uuid4(), owner)
    assert logs is None
    assert error == "NotFound"

    logs, error = await repo.get_execution_logs(uuid4(), other)
    assert logs is None
    assert error == "Forbidden"

    logs, error = await repo.get_execution_logs(uuid4(), owner)
    assert error is None
    assert logs is not None
    assert len(logs) == 1
    assert logs[0].timestamp == ""
    assert logs[0].level == "info"
    assert logs[0].message == ""
    assert logs[0].sequence == 0


@pytest.mark.asyncio
async def test_get_execution_variables_forbidden_and_not_found() -> None:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=OneOrNoneResult(None))
    repo = ExecutionRepository(session)

    variables, error = await repo.get_execution_variables(uuid4(), _user())
    assert variables is None
    assert error == "Forbidden"
    session.execute.assert_not_called()

    variables, error = await repo.get_execution_variables(uuid4(), _user(is_superuser=True))
    assert variables is None
    assert error == "NotFound"


@pytest.mark.asyncio
async def test_cancel_execution_handles_not_found_and_forbidden() -> None:
    owner = _user()
    other = _user()
    execution = _execution(status=ExecutionStatus.RUNNING.value, executed_by=owner.user_id)
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            None,
            ExecuteResult(scalar_row=None),
            None,
            ExecuteResult(scalar_row=execution),
        ]
    )
    repo = ExecutionRepository(session)

    result, error = await repo.cancel_execution(uuid4(), owner)
    assert result is None
    assert error == "NotFound"

    result, error = await repo.cancel_execution(execution.id, other)
    assert result is None
    assert error == "Forbidden"
    session.flush.assert_not_called()


@pytest.mark.asyncio
async def test_get_execution_result_and_variables_enforce_access() -> None:
    owner = _user()
    other = _user()
    row = SimpleNamespace(result={"ok": True}, result_type="json", executed_by=owner.user_id)
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[
        OneOrNoneResult(row),
        OneOrNoneResult(row),
        OneOrNoneResult((uuid4(), None)),
    ])
    repo = ExecutionRepository(session)

    result, error = await repo.get_execution_result(uuid4(), owner)
    assert error is None
    assert result == {"result": {"ok": True}, "result_type": "json"}

    result, error = await repo.get_execution_result(uuid4(), other)
    assert result is None
    assert error == "Forbidden"

    variables, error = await repo.get_execution_variables(uuid4(), _user(is_superuser=True))
    assert error is None
    assert variables == {}


@pytest.mark.asyncio
async def test_cancel_execution_rejects_terminal_status_and_publishes_running_cancel() -> None:
    owner = _user(is_superuser=True)
    done = _execution(status=ExecutionStatus.SUCCESS.value, executed_by=owner.user_id)
    running = _execution(status=ExecutionStatus.RUNNING.value, executed_by=owner.user_id)
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[
        None,
        MagicMock(scalar_one_or_none=MagicMock(return_value=done)),
        None,
        MagicMock(scalar_one_or_none=MagicMock(return_value=running)),
    ])
    repo = ExecutionRepository(session)

    result, error = await repo.cancel_execution(done.id, owner)
    assert result is None
    assert error == "BadRequest"

    publish = AsyncMock()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("src.core.pubsub.publish_execution_update", publish)
        result, error = await repo.cancel_execution(running.id, owner)

    assert error is None
    assert result is not None
    assert running.status == ExecutionStatus.CANCELLING.value
    publish.assert_awaited_once_with(
        execution_id=running.id,
        status=ExecutionStatus.CANCELLING.value,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial_status",
    [ExecutionStatus.SCHEDULED.value, ExecutionStatus.PENDING.value],
)
async def test_cancel_execution_immediately_cancels_unclaimed_work(
    initial_status: str,
) -> None:
    owner = _user(is_superuser=True)
    execution = _execution(status=initial_status, executed_by=owner.user_id)
    session = AsyncMock()
    session.execute.return_value = MagicMock(
        scalar_one_or_none=MagicMock(return_value=execution)
    )
    repo = ExecutionRepository(session)

    publish = AsyncMock()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("src.core.pubsub.publish_execution_update", publish)
        result, error = await repo.cancel_execution(execution.id, owner)

    assert error is None
    assert result is not None
    assert execution.status == ExecutionStatus.CANCELLED.value
    assert "bifrost:workflow-execution:" in str(session.execute.await_args_list[0].args[0])
    publish.assert_awaited_once_with(
        execution_id=execution.id,
        status=ExecutionStatus.CANCELLED.value,
    )


@pytest.mark.asyncio
async def test_standalone_create_execution_uses_provided_session_without_commit() -> None:
    session = AsyncMock()
    with patch.object(ExecutionRepository, "create_execution", AsyncMock()) as create:
        await executions.create_execution(
            execution_id=str(uuid4()),
            workflow_name="sync_ticket",
            parameters={"ticket": "123"},
            org_id="GLOBAL",
            user_id=str(uuid4()),
            user_name="Runner",
            session=session,
        )

    create.assert_awaited_once()
    assert create.await_args.args == ()
    assert create.await_args.kwargs["workflow_name"] == "sync_ticket"
    assert create.await_args.kwargs["parameters"] == {"ticket": "123"}
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_standalone_update_execution_uses_provided_session_without_commit() -> None:
    session = AsyncMock()
    execution_id = str(uuid4())
    with patch.object(ExecutionRepository, "update_execution", AsyncMock()) as update:
        await executions.update_execution(
            execution_id=execution_id,
            status=ExecutionStatus.SUCCESS,
            result={"ok": True},
            metrics={"process_rss_bytes": 1024},
            session=session,
        )

    update.assert_awaited_once()
    assert update.await_args.kwargs["execution_id"] == execution_id
    assert update.await_args.kwargs["status"] == ExecutionStatus.SUCCESS
    assert update.await_args.kwargs["result"] == {"ok": True}
    assert update.await_args.kwargs["metrics"] == {"process_rss_bytes": 1024}
    session.commit.assert_not_called()


class _AsyncSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_standalone_wrappers_create_session_and_commit_when_needed() -> None:
    create_session = AsyncMock()
    update_session = AsyncMock()
    session_factory = MagicMock(
        side_effect=[
            _AsyncSessionContext(create_session),
            _AsyncSessionContext(update_session),
        ]
    )

    with (
        patch("src.core.database.get_session_factory", return_value=session_factory),
        patch.object(ExecutionRepository, "create_execution", AsyncMock()) as create,
        patch.object(ExecutionRepository, "update_execution", AsyncMock()) as update,
    ):
        await executions.create_execution(
            execution_id=str(uuid4()),
            workflow_name="sync_ticket",
            parameters={},
            org_id=None,
            user_id=str(uuid4()),
            user_name="Runner",
        )
        await executions.update_execution(
            execution_id=str(uuid4()),
            status=ExecutionStatus.FAILED,
            error_message="boom",
        )

    assert session_factory.call_count == 2
    create.assert_awaited_once()
    update.assert_awaited_once()
    create_session.commit.assert_awaited_once()
    update_session.commit.assert_awaited_once()
