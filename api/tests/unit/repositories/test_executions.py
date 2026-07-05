from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
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
        "started_at": now,
        "completed_at": now,
        "variables": {"secret": "hidden"},
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
async def test_update_execution_sets_result_type_metrics_economics_and_logs() -> None:
    execution_id = uuid4()
    session = AsyncMock()
    session.add = MagicMock()
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

    statement = session.execute.await_args.args[0]
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
async def test_update_execution_classifies_json_text_and_default_result_types() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    repo = ExecutionRepository(session)

    for result, expected in [
        ({"ok": True}, "json"),
        (["a"], "json"),
        ("plain text", "text"),
        (123, "json"),
    ]:
        await repo.update_execution(str(uuid4()), ExecutionStatus.SUCCESS, result=result)
        statement = session.execute.await_args.args[0]
        assert statement.compile().params["result_type"] == expected


class OneOrNoneResult:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row


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
        MagicMock(scalar_one_or_none=MagicMock(return_value=done)),
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
