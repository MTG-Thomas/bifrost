from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from types import ModuleType, SimpleNamespace
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from src.services.mcp_server.tools import execution


def _context(*, user_id=None, org_id=None, admin=False):
    return SimpleNamespace(
        user_id=str(user_id or uuid4()),
        org_id=str(org_id or uuid4()),
        user_email="user@example.test",
        user_name="Unit User",
        is_platform_admin=admin,
    )


def _fake_tool_db(db):
    @asynccontextmanager
    async def fake_get_tool_db(_context):
        yield db

    return fake_get_tool_db


def _patch_execution_repository(repo):
    repositories_module = ModuleType("src.repositories")
    executions_module = ModuleType("src.repositories.executions")
    executions_module.ExecutionRepository = MagicMock(return_value=repo)
    repositories_module.executions = executions_module
    repositories_module.__path__ = []
    return patch.dict(
        sys.modules,
        {
            "src.repositories": repositories_module,
            "src.repositories.executions": executions_module,
        },
    )


class _Status(Enum):
    SUCCESS = "success"
    FAILED = "failed"


def _execution(**overrides):
    row = SimpleNamespace(
        execution_id=str(uuid4()),
        workflow_name="Ticket triage",
        status=_Status.SUCCESS,
        duration_ms=125,
        started_at=datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 7, 5, 12, 1, tzinfo=timezone.utc),
        error_message=None,
        result={"ok": True},
        logs=[{"level": "info", "message": "started"}],
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _text(result) -> str:
    content = result.content
    if isinstance(content, list):
        return "\n".join(getattr(item, "text", str(item)) for item in content)
    return str(content)


def test_context_to_user_principal_normalizes_string_ids():
    user_id = uuid4()
    org_id = uuid4()

    principal = execution._context_to_user_principal(
        _context(user_id=user_id, org_id=org_id, admin=True)
    )

    assert principal.user_id == user_id
    assert principal.organization_id == org_id
    assert principal.email == "user@example.test"
    assert principal.name == "Unit User"
    assert principal.is_superuser is True


@pytest.mark.asyncio
async def test_list_executions_passes_filters_and_truncates_long_errors():
    db = AsyncMock()
    repo = MagicMock()
    long_error = "x" * 120
    repo.list_executions = AsyncMock(
        return_value=(
            [
                _execution(error_message=long_error),
                _execution(
                    workflow_name=None,
                    status="queued",
                    started_at=None,
                    error_message="waiting",
                ),
            ],
            2,
        )
    )
    ctx = _context()

    with (
        patch.object(execution, "get_tool_db", _fake_tool_db(db)),
        _patch_execution_repository(repo),
    ):
        result = await execution.list_executions(
            ctx,
            workflow_name="Ticket triage",
            status="success",
            limit=5,
        )

    assert result.structured_content["count"] == 2
    first, second = result.structured_content["executions"]
    assert first["status"] == "success"
    assert first["created_at"] == "2026-07-05T12:00:00+00:00"
    assert first["error"] == ("x" * 100) + "..."
    assert second["workflow_name"] == "Unknown"
    assert second["status"] == "queued"
    assert second["created_at"] is None
    assert "Found 2 execution" in _text(result)
    call_kwargs = repo.list_executions.await_args.kwargs
    assert call_kwargs["workflow_name"] == "Ticket triage"
    assert call_kwargs["status_filter"] == "success"
    assert call_kwargs["limit"] == 5
    assert call_kwargs["org_id"] == UUID(ctx.org_id)
    assert call_kwargs["user"].user_id == UUID(ctx.user_id)


@pytest.mark.asyncio
async def test_list_executions_handles_empty_and_repository_errors():
    db = AsyncMock()
    repo = MagicMock()
    repo.list_executions = AsyncMock(return_value=([], 0))

    with (
        patch.object(execution, "get_tool_db", _fake_tool_db(db)),
        _patch_execution_repository(repo),
    ):
        empty = await execution.list_executions(_context())

    assert empty.structured_content == {"executions": [], "count": 0}

    repo.list_executions = AsyncMock(side_effect=RuntimeError("database offline"))
    with (
        patch.object(execution, "get_tool_db", _fake_tool_db(db)),
        _patch_execution_repository(repo),
    ):
        failed = await execution.list_executions(_context())

    assert failed.structured_content["error"] == (
        "Error listing executions: database offline"
    )


@pytest.mark.asyncio
async def test_get_execution_serializes_details_and_last_twenty_logs():
    db = AsyncMock()
    repo = MagicMock()
    execution_id = uuid4()
    logs = [{"level": "debug", "message": f"log {i}"} for i in range(25)]
    repo.get_execution = AsyncMock(
        return_value=(
            _execution(
                execution_id=str(execution_id),
                status="running",
                logs=logs,
                completed_at=None,
            ),
            None,
        )
    )
    ctx = _context()

    with (
        patch.object(execution, "get_tool_db", _fake_tool_db(db)),
        _patch_execution_repository(repo),
    ):
        result = await execution.get_execution(ctx, str(execution_id))

    assert result.structured_content["id"] == str(execution_id)
    assert result.structured_content["status"] == "running"
    assert result.structured_content["completed_at"] is None
    assert result.structured_content["logs"][0]["message"] == "log 5"
    assert result.structured_content["logs"][-1]["message"] == "log 24"
    assert "Execution: Ticket triage (running)" in _text(result)
    assert repo.get_execution.await_args.kwargs["execution_id"] == execution_id
    assert repo.get_execution.await_args.kwargs["user"].user_id == UUID(ctx.user_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_code", "expected"),
    [
        ("NotFound", "Execution not found"),
        ("Forbidden", "Access denied"),
        (None, "Execution not found"),
    ],
)
async def test_get_execution_handles_repository_denials(error_code, expected):
    db = AsyncMock()
    repo = MagicMock()
    repo.get_execution = AsyncMock(return_value=(None, error_code))

    with (
        patch.object(execution, "get_tool_db", _fake_tool_db(db)),
        _patch_execution_repository(repo),
    ):
        result = await execution.get_execution(_context(), str(uuid4()))

    assert expected in result.structured_content["error"]


@pytest.mark.asyncio
async def test_get_execution_reports_repository_errors():
    db = AsyncMock()
    repo = MagicMock()
    repo.get_execution = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch.object(execution, "get_tool_db", _fake_tool_db(db)),
        _patch_execution_repository(repo),
    ):
        result = await execution.get_execution(_context(), str(uuid4()))

    assert result.structured_content["error"] == "Error getting execution: boom"
