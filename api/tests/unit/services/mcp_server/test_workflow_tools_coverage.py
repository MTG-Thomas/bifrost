from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.tools import workflow


def _context(*, admin: bool = False, org_id=None, user_id=None) -> SimpleNamespace:
    return SimpleNamespace(
        is_platform_admin=admin,
        org_id=org_id if org_id is not None else uuid4(),
        user_id=user_id if user_id is not None else uuid4(),
        is_external=False,
        user_email="admin@example.com" if admin else "user@example.com",
        user_name="Admin" if admin else "User",
    )


def _fake_tool_db(db):
    @asynccontextmanager
    async def fake_get_tool_db(_context):
        yield db

    return fake_get_tool_db


def _workflow(**overrides):
    row = SimpleNamespace(
        id=uuid4(),
        name="Ticket triage",
        description="Route ticket intake",
        type="workflow",
        category="support",
        endpoint_enabled=True,
        path="workflows/ticket.py",
        is_active=True,
        tool_description=None,
        parameters_schema={"type": "object"},
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class TestWorkflowListAndGet:
    @pytest.mark.asyncio
    async def test_list_workflows_formats_results_and_repo_context(self):
        db = AsyncMock()
        repo = MagicMock()
        repo.search = AsyncMock(
            return_value=[
                _workflow(name="Ticket triage"),
                _workflow(name="Escalate", description=None, type="tool"),
            ]
        )
        repo.count_active = AsyncMock(return_value=12)
        org_id = uuid4()
        user_id = uuid4()

        with (
            patch.object(workflow, "get_tool_db", _fake_tool_db(db)),
            patch(
                "src.repositories.workflows.WorkflowRepository",
                return_value=repo,
            ) as repo_cls,
        ):
            result = await workflow.list_workflows(
                _context(org_id=str(org_id), user_id=str(user_id)),
                query="ticket",
                category="support",
            )

        assert result.structured_content["count"] == 2
        assert result.structured_content["total_count"] == 12
        assert result.structured_content["workflows"][0]["name"] == "Ticket triage"
        assert "Found 2 workflow" in result.content
        repo.search.assert_awaited_once_with(
            query="ticket",
            category="support",
            limit=100,
        )
        assert repo_cls.call_args.kwargs["org_id"] == org_id
        assert repo_cls.call_args.kwargs["user_id"] == user_id
        assert repo_cls.call_args.kwargs["is_superuser"] is False

    @pytest.mark.asyncio
    async def test_list_workflows_returns_empty_and_reports_errors(self):
        db = AsyncMock()
        repo = MagicMock()
        repo.search = AsyncMock(return_value=[])
        repo.count_active = AsyncMock(return_value=0)

        with (
            patch.object(workflow, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository", return_value=repo),
        ):
            empty = await workflow.list_workflows(_context())

        assert empty.structured_content["workflows"] == []
        assert "No workflows found" in empty.content

        repo.search = AsyncMock(side_effect=RuntimeError("database down"))
        with (
            patch.object(workflow, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository", return_value=repo),
        ):
            failed = await workflow.list_workflows(_context())

        assert "Error listing workflows" in failed.structured_content["error"]
        assert "database down" in failed.structured_content["error"]

    @pytest.mark.asyncio
    async def test_get_workflow_validates_identifier_and_formats_tool_metadata(self):
        missing = await workflow.get_workflow(_context())
        assert "Either workflow_id or workflow_name is required" in (
            missing.structured_content["error"]
        )

        db = AsyncMock()
        repo = MagicMock()
        row = _workflow(type="tool", tool_description="Use this for triage")
        repo.get = AsyncMock(return_value=row)

        with (
            patch.object(workflow, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository", return_value=repo),
        ):
            result = await workflow.get_workflow(_context(), workflow_id=str(row.id))

        assert result.structured_content["id"] == str(row.id)
        assert result.structured_content["tool_description"] == "Use this for triage"
        assert result.structured_content["parameters"] == {"type": "object"}
        assert "Workflow: Ticket triage" in result.content

        repo.get = AsyncMock(side_effect=ValueError("bad id"))
        with (
            patch.object(workflow, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository", return_value=repo),
        ):
            bad = await workflow.get_workflow(_context(), workflow_id="not-a-uuid")

        assert "Invalid workflow_id format" in bad.structured_content["error"]


class TestWorkflowExecuteAndValidate:
    @pytest.mark.asyncio
    async def test_execute_workflow_reports_success_and_failure_results(self):
        db = AsyncMock()
        repo = MagicMock()
        row = _workflow()
        repo.resolve = AsyncMock(return_value=row)
        success_status = SimpleNamespace(value="Success")
        failure_status = SimpleNamespace(value="Failed")

        async def execute_success(**kwargs):
            assert kwargs["workflow_id"] == str(row.id)
            assert kwargs["parameters"] == {"ticket_id": 123}
            return SimpleNamespace(
                status=success_status,
                execution_id="exec-1",
                duration_ms=42,
                result={"ok": True},
                error=None,
                error_type=None,
            )

        with (
            patch.object(workflow, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository", return_value=repo),
            patch("src.services.execution.service.execute_tool", execute_success),
        ):
            ok = await workflow.execute_workflow(
                _context(),
                str(row.id),
                {"ticket_id": 123},
            )

        assert ok.structured_content["success"] is True
        assert ok.structured_content["result"] == {"ok": True}
        assert "completed successfully" in ok.content

        async def execute_failure(**_kwargs):
            return SimpleNamespace(
                status=failure_status,
                execution_id="exec-2",
                duration_ms=7,
                result=None,
                error="boom",
                error_type="RuntimeError",
            )

        with (
            patch.object(workflow, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository", return_value=repo),
            patch("src.services.execution.service.execute_tool", execute_failure),
        ):
            failed = await workflow.execute_workflow(_context(), str(row.id))

        assert failed.structured_content["success"] is False
        assert failed.structured_content["error"] == "boom"
        assert "failed: boom" in failed.content

    @pytest.mark.asyncio
    async def test_execute_workflow_requires_and_resolves_workflow(self):
        missing_id = await workflow.execute_workflow(_context(), "")
        assert "workflow_id is required" in missing_id.structured_content["error"]

        db = AsyncMock()
        repo = MagicMock()
        repo.resolve = AsyncMock(return_value=None)
        with (
            patch.object(workflow, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository", return_value=repo),
        ):
            not_found = await workflow.execute_workflow(_context(), "missing")

        assert "Workflow 'missing' not found" in not_found.structured_content["error"]

    @pytest.mark.asyncio
    async def test_validate_workflow_reports_valid_syntax_errors_and_missing_files(self):
        class Storage:
            def __init__(self, _db):
                pass

            async def read_file(self, path):
                if path == "workflows/valid.py":
                    return (
                        b"from bifrost import workflow\n"
                        b"@workflow\n"
                        b"async def run():\n"
                        b"    return {}\n"
                    ), None
                if path == "workflows/bad.py":
                    return b"from bifrost import workflow\nasync def run(:\n    pass\n", None
                if path == "workflows/plain.py":
                    return b"async def run():\n    return {}\n", None
                raise FileNotFoundError(path)

        with (
            patch.object(workflow, "get_tool_db", _fake_tool_db(AsyncMock())),
            patch("src.services.file_storage.FileStorageService", Storage),
        ):
            valid = await workflow.validate_workflow(_context(), "workflows/valid.py")
            syntax = await workflow.validate_workflow(_context(), "workflows/bad.py")
            plain = await workflow.validate_workflow(_context(), "workflows/plain.py")
            missing = await workflow.validate_workflow(_context(), "workflows/missing.py")

        assert valid.structured_content["valid"] is True
        assert valid.structured_content["workflow_functions"] == ["run"]
        assert syntax.structured_content["valid"] is False
        assert syntax.structured_content["errors"][0]["type"] == "syntax"
        assert plain.structured_content["valid"] is False
        assert "No @workflow decorator" in plain.structured_content["errors"][0]
        assert "File not found" in missing.structured_content["error"]
