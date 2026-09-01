from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.tools import workflow


@pytest.fixture(autouse=True)
def _legacy_workspace_authority(monkeypatch):
    monkeypatch.setattr(
        workflow,
        "active_workspace_release_file_view",
        AsyncMock(return_value=None),
    )


def _content_text(result) -> str:
    content = result.content
    if isinstance(content, list):
        return "\n".join(getattr(item, "text", str(item)) for item in content)
    return str(content)


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


def _fake_rest_client(http=None):
    @asynccontextmanager
    async def fake_client(_context):
        yield http or AsyncMock()

    return fake_client


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


@pytest.mark.asyncio
async def test_authoritative_workflow_read_uses_immutable_live(monkeypatch):
    path = "workflows/governed.py"
    view = MagicMock()
    view.governs.return_value = True
    view.read = AsyncMock(return_value=b"reviewed = True\n")
    monkeypatch.setattr(
        workflow,
        "active_workspace_release_file_view",
        AsyncMock(return_value=view),
    )

    content = await workflow._read_authoritative_workspace_file(
        object(), _context(), path
    )

    assert content == b"reviewed = True\n"
    view.read.assert_awaited_once_with(path)


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
        assert "Found 2 workflow" in _content_text(result)
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
        assert "No workflows found" in _content_text(empty)

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
        assert (
            "Either workflow_id or workflow_name is required"
            in (missing.structured_content["error"])
        )

        db = AsyncMock()
        repo = MagicMock()
        nested_schema = {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                }
            },
            "required": ["filters"],
        }
        row = _workflow(
            type="tool",
            tool_description="Use this for triage",
            parameters_schema=nested_schema,
        )
        repo.get = AsyncMock(return_value=row)

        with (
            patch.object(workflow, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository", return_value=repo),
        ):
            result = await workflow.get_workflow(_context(), workflow_id=str(row.id))

        assert result.structured_content["id"] == str(row.id)
        assert result.structured_content["tool_description"] == "Use this for triage"
        assert result.structured_content["parameters"] == nested_schema
        assert "Workflow: Ticket triage" in _content_text(result)

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
        assert "completed successfully" in _content_text(ok)

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
        assert "failed: boom" in _content_text(failed)

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
    async def test_validate_workflow_reports_valid_syntax_errors_and_missing_files(
        self,
    ):
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
                    return (
                        b"from bifrost import workflow\nasync def run(:\n    pass\n",
                        None,
                    )
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
            missing = await workflow.validate_workflow(
                _context(), "workflows/missing.py"
            )

        assert valid.structured_content["valid"] is True
        assert valid.structured_content["workflow_functions"] == ["run"]
        assert syntax.structured_content["valid"] is False
        assert syntax.structured_content["errors"][0]["type"] == "syntax"
        assert plain.structured_content["valid"] is False
        assert "No @workflow decorator" in plain.structured_content["errors"][0]
        assert "File not found" in missing.structured_content["error"]

    @pytest.mark.asyncio
    async def test_execute_workflow_reports_mocked_service_errors(self):
        db = AsyncMock()
        repo = MagicMock()
        repo.resolve = AsyncMock(return_value=_workflow())

        async def execute_raises(**_kwargs):
            raise RuntimeError("executor offline")

        with (
            patch.object(workflow, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository", return_value=repo),
            patch("src.services.execution.service.execute_tool", execute_raises),
        ):
            result = await workflow.execute_workflow(_context(), "ticket_triage")

        assert "Error executing workflow" in result.structured_content["error"]
        assert "executor offline" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_validate_workflow_reports_storage_service_errors(self):
        class Storage:
            def __init__(self, _db):
                pass

            async def read_file(self, _path):
                raise RuntimeError("storage unavailable")

        with (
            patch.object(workflow, "get_tool_db", _fake_tool_db(AsyncMock())),
            patch("src.services.file_storage.FileStorageService", Storage),
        ):
            result = await workflow.validate_workflow(_context(), "workflows/error.py")

        assert "Error validating workflow" in result.structured_content["error"]
        assert "storage unavailable" in result.structured_content["error"]


class TestWorkflowLifecycleWrappers:
    @pytest.mark.asyncio
    async def test_update_workflow_validation_scope_failure_and_result_serialization(
        self,
    ):
        missing = await workflow.update_workflow(_context(), "")
        assert "workflow_ref is required" in missing.structured_content["error"]

        class FailingResolver:
            def __init__(self, _http):
                pass

            async def resolve(self, kind, ref):
                assert kind == "workflow"
                assert ref == "foreign"
                raise RuntimeError("not in caller scope")

        with (
            patch.object(workflow, "rest_client", _fake_rest_client()),
            patch("bifrost.refs.RefResolver", FailingResolver),
        ):
            unresolved = await workflow.update_workflow(_context(), "foreign")

        assert (
            "could not resolve workflow 'foreign'"
            in unresolved.structured_content["error"]
        )
        assert unresolved.structured_content["detail"] == "not in caller scope"

        class Resolver:
            def __init__(self, _http):
                pass

            async def resolve(self, kind, ref):
                assert kind == "workflow"
                return "workflow-uuid"

        async def assemble_raises(*_args, **_kwargs):
            raise ValueError("role id is invalid")

        with (
            patch.object(workflow, "rest_client", _fake_rest_client()),
            patch("bifrost.refs.RefResolver", Resolver),
            patch("bifrost.dto_flags.assemble_body", assemble_raises),
        ):
            invalid = await workflow.update_workflow(
                _context(),
                "ticket_triage",
                role_ids=["bad-role"],
            )

        assert "invalid input" in invalid.structured_content["error"]
        assert invalid.structured_content["detail"] == "role id is invalid"

        async def assemble_body(_model, fields, **_kwargs):
            assert fields["description"] == "Updated"
            assert fields["role_ids"] is None
            return {"description": fields["description"]}

        call_rest_mock = AsyncMock(return_value=(200, "ok"))
        ctx = _context()
        with (
            patch.object(workflow, "rest_client", _fake_rest_client()),
            patch("bifrost.refs.RefResolver", Resolver),
            patch("bifrost.dto_flags.assemble_body", assemble_body),
            patch.object(workflow, "call_rest", call_rest_mock),
        ):
            success = await workflow.update_workflow(
                ctx,
                "ticket_triage",
                description="Updated",
            )

        call_rest_mock.assert_awaited_once_with(
            ctx,
            "PATCH",
            "/api/workflows/workflow-uuid",
            json_body={"description": "Updated"},
        )
        assert success.structured_content == {"body": "ok"}
        assert "Updated workflow workflow-uuid" in _content_text(success)

    @pytest.mark.asyncio
    async def test_delete_workflow_serializes_force_conflict_and_http_errors(self):
        missing = await workflow.delete_workflow(_context(), "")
        assert "workflow_ref is required" in missing.structured_content["error"]

        class Resolver:
            def __init__(self, _http):
                pass

            async def resolve(self, kind, _ref):
                assert kind == "workflow"
                return "workflow-uuid"

        call_rest = AsyncMock(return_value=(409, {"dependencies": ["execution"]}))
        ctx = _context()
        with (
            patch.object(workflow, "rest_client", _fake_rest_client()),
            patch("bifrost.refs.RefResolver", Resolver),
            patch.object(workflow, "call_rest", call_rest),
        ):
            conflict = await workflow.delete_workflow(ctx, "ticket_triage")

        assert "workflow has dependencies" in conflict.structured_content["error"]
        assert conflict.structured_content["dependencies"] == ["execution"]
        call_rest.assert_awaited_once_with(
            ctx,
            "DELETE",
            "/api/workflows/workflow-uuid",
            json_body=None,
        )

        call_rest = AsyncMock(return_value=(500, "server error"))
        with (
            patch.object(workflow, "rest_client", _fake_rest_client()),
            patch("bifrost.refs.RefResolver", Resolver),
            patch.object(workflow, "call_rest", call_rest),
        ):
            failed = await workflow.delete_workflow(
                _context(),
                "ticket_triage",
                force_deactivation=True,
            )

        assert "delete_workflow failed: HTTP 500" in failed.structured_content["error"]
        assert failed.structured_content["body"] == "server error"
        assert call_rest.call_args.kwargs["json_body"] == {"force_deactivation": True}

    @pytest.mark.asyncio
    async def test_grant_and_revoke_workflow_role_validation_scope_and_http_errors(
        self,
    ):
        missing_workflow = await workflow.grant_workflow_role(_context(), "", "role")
        assert (
            "workflow_ref is required" in missing_workflow.structured_content["error"]
        )

        missing_role = await workflow.revoke_workflow_role(_context(), "workflow", "")
        assert "role_ref is required" in missing_role.structured_content["error"]

        class RoleFailingResolver:
            def __init__(self, _http):
                pass

            async def resolve(self, kind, _ref):
                if kind == "workflow":
                    return "workflow-uuid"
                raise RuntimeError("role outside scope")

        with (
            patch.object(workflow, "rest_client", _fake_rest_client()),
            patch("bifrost.refs.RefResolver", RoleFailingResolver),
        ):
            unresolved = await workflow.grant_workflow_role(
                _context(),
                "ticket_triage",
                "restricted",
            )

        assert (
            "could not resolve role 'restricted'"
            in unresolved.structured_content["error"]
        )
        assert unresolved.structured_content["detail"] == "role outside scope"

        class Resolver:
            def __init__(self, _http):
                pass

            async def resolve(self, kind, _ref):
                return f"{kind}-uuid"

        grant_call = AsyncMock(return_value=(403, {"detail": "forbidden"}))
        with (
            patch.object(workflow, "rest_client", _fake_rest_client()),
            patch("bifrost.refs.RefResolver", Resolver),
            patch.object(workflow, "call_rest", grant_call),
        ):
            grant_failed = await workflow.grant_workflow_role(
                _context(),
                "ticket_triage",
                "operators",
            )

        assert (
            "grant_workflow_role failed: HTTP 403"
            in grant_failed.structured_content["error"]
        )
        assert grant_failed.structured_content["body"] == {"detail": "forbidden"}

        revoke_call = AsyncMock(return_value=(200, {}))
        with (
            patch.object(workflow, "rest_client", _fake_rest_client()),
            patch("bifrost.refs.RefResolver", Resolver),
            patch.object(workflow, "call_rest", revoke_call),
        ):
            revoked = await workflow.revoke_workflow_role(
                _context(),
                "ticket_triage",
                "operators",
            )

        assert revoked.structured_content == {
            "workflow_id": "workflow-uuid",
            "role_id": "role-uuid",
        }
        assert revoke_call.call_args.args[1:3] == (
            "DELETE",
            "/api/workflows/workflow-uuid/roles/role-uuid",
        )
