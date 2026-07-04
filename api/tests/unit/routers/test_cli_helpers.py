from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
from src.routers import cli


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class ExecuteDb:
    def __init__(self, value):
        self.value = value
        self.execute = AsyncMock(return_value=ScalarResult(value))


def _principal(*, org_id: UUID | None = None, is_superuser: bool = False) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="user@example.com",
        organization_id=org_id,
        is_superuser=is_superuser,
    )


def test_parse_sdk_scope_handles_unset_global_uuid_and_bad_value() -> None:
    from shared.scope_resolver import UNSET

    assert cli._parse_sdk_scope(None) is UNSET
    assert cli._parse_sdk_scope("") is UNSET
    assert cli._parse_sdk_scope("global") is None
    org_id = uuid4()
    assert cli._parse_sdk_scope(str(org_id)) == org_id

    with pytest.raises(Exception) as exc:
        cli._parse_sdk_scope("not-a-scope")
    error = cast(Any, exc.value)
    assert error.status_code == 422
    assert "scope must be" in error.detail


def test_principal_org_id_normalizes_uuid_string_and_none() -> None:
    org_id = uuid4()

    assert cli._principal_org_id(_principal(org_id=org_id)) == org_id
    assert cli._principal_org_id(cast(Any, SimpleNamespace(organization_id=str(org_id)))) == org_id
    assert cli._principal_org_id(_principal(org_id=None)) is None


@pytest.mark.asyncio
async def test_is_provider_org_accepts_awaitable_scalar() -> None:
    class AwaitableScalar:
        async def _value(self):
            return True

        def __await__(self):
            return self._value().__await__()

    assert await cli._is_provider_org(ExecuteDb(AwaitableScalar()), uuid4()) is True


@pytest.mark.asyncio
async def test_is_external_user_db_uses_principal_flag_before_db() -> None:
    principal = _principal(org_id=uuid4())
    principal.is_external = True
    db = ExecuteDb(False)

    assert await cli._is_external_user_db(principal, db) is True
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_is_external_user_db_handles_missing_or_non_external_user() -> None:
    principal = _principal(org_id=uuid4())
    principal.is_external = False
    invalid_principal = SimpleNamespace(is_external=False, user_id=object())
    assert await cli._is_external_user_db(cast(Any, invalid_principal), ExecuteDb(True)) is False

    principal.user_id = uuid4()
    assert await cli._is_external_user_db(principal, ExecuteDb(True)) is True
    assert await cli._is_external_user_db(principal, ExecuteDb(False)) is False


def test_resolve_requested_oauth_scopes_defaults_filters_and_rejects() -> None:
    provider = SimpleNamespace(scopes=["read", "write", ""])

    assert cli._resolve_requested_oauth_scopes(provider, None) == "read write"
    assert cli._resolve_requested_oauth_scopes(provider, "write read") == "write read"
    assert cli._resolve_requested_oauth_scopes(provider, "   ") == "read write"

    with pytest.raises(ValueError, match="not configured"):
        cli._resolve_requested_oauth_scopes(provider, "admin read")


def test_session_to_response_sorts_loaded_executions_and_workflows() -> None:
    session_id = uuid4()
    user_id = uuid4()
    now = datetime.now(timezone.utc)
    executions = [
        SimpleNamespace(
            id=uuid4(),
            workflow_name="old",
            status=ExecutionStatus.SUCCESS,
            created_at=now.replace(year=2025),
            duration_ms=10,
        ),
        SimpleNamespace(
            id=uuid4(),
            workflow_name="new",
            status="Running",
            created_at=now,
            duration_ms=None,
        ),
    ]
    session = SimpleNamespace(
        id=session_id,
        user_id=user_id,
        file_path="workflows/a.py",
        workflows=[{"name": "wf", "description": "desc", "parameters": [{"name": "x", "type": "string", "required": False}]}],
        selected_workflow="wf",
        params={"x": 1},
        pending=True,
        last_seen=now,
        created_at=now,
        executions=executions,
    )

    with patch("src.routers.cli.sa_inspect", create=True) as _unused:
        # The helper imports sqlalchemy.inspect locally, so patch that symbol instead.
        pass

    class Inspection:
        dict = {"executions": executions}

    with patch("sqlalchemy.inspect", return_value=Inspection()):
        response = cli._session_to_response(session, is_connected=True)

    assert response.id == str(session_id)
    assert response.is_connected is True
    assert [item.workflow_name for item in response.executions] == ["new", "old"]
    assert response.executions[1].status == "Success"
    assert response.workflows[0].name == "wf"
