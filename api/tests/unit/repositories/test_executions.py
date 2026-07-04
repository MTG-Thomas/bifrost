from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

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
