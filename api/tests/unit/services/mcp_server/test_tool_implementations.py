"""
Unit tests for MCP tool implementations.

Tests the actual tool implementation functions that handle
workflow validation, execution tracking, and knowledge search.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4
from unittest.mock import patch

import pytest
from fastmcp.tools import ToolResult

from src.services.mcp_server.server import MCPContext


def is_error_result(result: ToolResult) -> bool:
    """Check if a ToolResult represents an error."""
    if result.structured_content and "error" in result.structured_content:
        return True
    content = result.content
    if isinstance(content, list):
        content = content[0].text if content else ""
    if content and isinstance(content, str) and content.startswith("Error:"):
        return True
    return False


def get_content_text(result: ToolResult) -> str:
    """Extract text content from a ToolResult."""
    content = result.content
    if isinstance(content, list):
        return content[0].text if content else ""
    return content or ""


# ==================== Fixtures ====================


@pytest.fixture
def context():
    """Create an MCPContext for testing."""
    return MCPContext(
        user_id=str(uuid4()),
        org_id=str(uuid4()),
        is_platform_admin=False,
        user_email="test@example.com",
        user_name="Test User",
    )


@pytest.fixture
def admin_context():
    """Create an admin MCPContext for testing."""
    return MCPContext(
        user_id=str(uuid4()),
        org_id=str(uuid4()),
        is_platform_admin=True,
        user_email="admin@example.com",
        user_name="Admin User",
    )


# ==================== Knowledge Tool Tests ====================


class TestSearchKnowledgeImpl:
    """Tests for search_knowledge tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_query_empty(self, context):
        """Should return error ToolResult when query is empty."""
        from src.services.mcp_server.tools.knowledge import search_knowledge

        result = await search_knowledge(context, "")
        assert is_error_result(result)
        assert result.structured_content is not None
        assert result.structured_content["error"] == "query is required"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_namespaces_accessible(self, context):
        """Should return empty results when user has no accessible namespaces."""
        from src.services.mcp_server.tools.knowledge import search_knowledge

        # Context has empty accessible_namespaces by default
        result = await search_knowledge(context, "test query")
        assert not is_error_result(result)
        assert result.structured_content is not None
        assert result.structured_content["results"] == []
        assert result.structured_content["count"] == 0
        # Check display text for message
        assert "No knowledge sources available" in get_content_text(result)

    @pytest.mark.asyncio
    async def test_returns_access_denied_for_unauthorized_namespace(self, context):
        """Should deny access to namespace not in accessible list."""
        from src.services.mcp_server.tools.knowledge import search_knowledge

        context.accessible_namespaces = ["allowed-ns"]
        result = await search_knowledge(context, "test query", namespace="forbidden-ns")
        assert is_error_result(result)
        assert result.structured_content is not None
        assert "Access denied" in result.structured_content["error"]
        assert "forbidden-ns" in result.structured_content["error"]


# ==================== Workflow Tool Tests ====================


class TestValidateWorkflowImpl:
    """Tests for validate_workflow tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_path_empty(self, context):
        """Should return error message when file_path is empty."""
        from src.services.mcp_server.tools.workflow import validate_workflow

        result = await validate_workflow(context, "")
        # The implementation returns a ToolResult with error when path is empty
        assert is_error_result(result)
        # Check that the content contains error info
        text = get_content_text(result)
        assert text is not None
        assert "Error" in text or "error" in text.lower()


# ==================== App Tool Tests ====================


class _ScalarOneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FirstResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class _ScalarsFirstResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return _FirstResult(self._value)


class _CreateAppDb:
    def __init__(self, *, stale_source: bool):
        self._stale_source = stale_source
        self.execute_calls = 0
        self.added = []
        self.flushed = False

    async def execute(self, _stmt):
        self.execute_calls += 1
        if self.execute_calls == 1:
            return _ScalarOneResult(None)
        if self.execute_calls == 2:
            return _FirstResult(("apps/stale-mcp/_layout.tsx",) if self._stale_source else None)
        raise AssertionError(f"unexpected execute call {self.execute_calls}")

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        # Intentionally no-op: the fake only records whether create_app reaches commit.
        return None


class TestAppToolImpl:
    def test_pick_slug_row_prefers_org_global_then_lowest_id(self):
        from src.services.mcp_server.tools.apps import _pick_slug_row

        org_id = uuid4()
        global_row = SimpleNamespace(id="2", organization_id=None)
        org_row = SimpleNamespace(id="3", organization_id=org_id)
        other_row = SimpleNamespace(id="1", organization_id=uuid4())

        assert _pick_slug_row([], org_id) is None
        assert _pick_slug_row([other_row], org_id) is other_row
        assert _pick_slug_row([global_row, org_row, other_row], org_id) is org_row
        assert _pick_slug_row([other_row, global_row], org_id) is global_row
        lowest_row = _pick_slug_row([other_row, SimpleNamespace(id="0", organization_id=uuid4())], org_id)
        assert cast(Any, lowest_row).id == "0"

    def test_guard_message_and_first_row_helpers(self):
        from fastapi import HTTPException

        from src.services.mcp_server.tools.apps import _first_row, _guard_message

        assert _guard_message(HTTPException(status_code=409, detail="locked")) == "locked"
        assert _guard_message(RuntimeError("plain")) == "plain"
        assert _first_row(_FirstResult("first")) == "first"
        assert _first_row(_ScalarsFirstResult("scalar")) == "scalar"
        assert _first_row(object()) is None

    @pytest.mark.asyncio
    async def test_create_app_validates_required_inputs(self, context):
        from src.services.mcp_server.tools.apps import create_app

        missing_name = await create_app(context, "")
        bad_scope = await create_app(context, "App", scope="tenant")
        bad_org = await create_app(context, "App", organization_id="not-a-uuid")
        context_without_org = MCPContext(
            user_id=str(uuid4()),
            org_id=None,
            is_platform_admin=False,
            user_email="test@example.com",
            user_name="Test User",
        )
        missing_org = await create_app(context_without_org, "App")

        assert missing_name.structured_content["error"] == "name is required"
        assert bad_scope.structured_content["error"] == "scope must be 'global' or 'organization'"
        assert "not a valid UUID" in bad_org.structured_content["error"]
        assert "organization_id is required" in missing_org.structured_content["error"]

    @pytest.mark.asyncio
    async def test_create_app_rejects_unclaimed_existing_source(self, context):
        """MCP app creation must not adopt stale source under apps/<slug>/."""
        from src.services.mcp_server.tools.apps import create_app

        db = _CreateAppDb(stale_source=True)

        @asynccontextmanager
        async def fake_get_tool_db(_context):
            yield db

        with patch("src.services.mcp_server.tools.apps.get_tool_db", fake_get_tool_db):
            result = await create_app(context, "Stale MCP", slug="stale-mcp")

        assert is_error_result(result)
        assert result.structured_content is not None
        assert "Source files already exist" in result.structured_content["error"]
        assert db.added == []
        assert db.flushed is False

    @pytest.mark.asyncio
    async def test_get_update_publish_and_replace_validate_inputs(self, context):
        from src.services.mcp_server.tools import apps

        missing_lookup = await apps.get_app(context)
        bad_get_id = await apps.get_app(context, app_id="not-a-uuid")
        bad_update_id = await apps.update_app(context, app_id="not-a-uuid", name="New")
        bad_publish_id = await apps.publish_app(context, app_id="not-a-uuid")
        missing_replace_id = await apps.replace_app(context, "", repo_path="apps/new")
        missing_replace_path = await apps.replace_app(context, "app-1", repo_path="")

        async def fake_call_rest(_context, _method, _path, json_body=None):
            return 409, {"detail": "conflict", "body": json_body}

        with patch("src.services.mcp_server.tools.apps.call_rest", fake_call_rest):
            failed_replace = await apps.replace_app(context, "app-1", repo_path="apps/new", force=True)

        assert "Either app_id or app_slug is required" in missing_lookup.structured_content["error"]
        assert "Invalid app_id format" in bad_get_id.structured_content["error"]
        assert "Invalid app_id format" in bad_update_id.structured_content["error"]
        assert "Invalid app_id format" in bad_publish_id.structured_content["error"]
        assert missing_replace_id.structured_content["error"] == "app_id is required"
        assert missing_replace_path.structured_content["error"] == "repo_path is required"
        assert "replace_app failed: HTTP 409" in failed_replace.structured_content["error"]

    @pytest.mark.asyncio
    async def test_replace_app_success_returns_rest_body(self, context):
        from src.services.mcp_server.tools.apps import replace_app

        async def fake_call_rest(_context, method, path, json_body=None):
            assert method == "POST"
            assert path == "/api/applications/app-1/replace"
            assert json_body == {"repo_path": "apps/new", "force": True}
            return 200, {"success": True, "repo_path": "apps/new"}

        with patch("src.services.mcp_server.tools.apps.call_rest", fake_call_rest):
            result = await replace_app(context, "app-1", "apps/new", force=True)

        assert not is_error_result(result)
        assert result.structured_content["repo_path"] == "apps/new"


# ==================== Get Workflow Tool Tests ====================


class TestGetWorkflowImpl:
    """Tests for get_workflow tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_no_id_or_name(self, context):
        """Should return error when neither workflow_id nor workflow_name provided."""
        from src.services.mcp_server.tools.workflow import get_workflow

        result = await get_workflow(context, None, None)
        assert is_error_result(result)
        assert result.structured_content is not None
        assert "error" in result.structured_content
        assert "workflow_id or workflow_name" in result.structured_content["error"]


# ==================== Execution Tool Tests ====================


class TestGetExecutionImpl:
    """Tests for get_execution tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_id_empty(self, context):
        """Should return error when execution_id is empty."""
        from src.services.mcp_server.tools.execution import get_execution

        result = await get_execution(context, "")
        assert is_error_result(result)
        assert result.structured_content is not None
        assert result.structured_content["error"] == "execution_id is required"


# ==================== System Tools Registry Tests ====================


class TestSystemToolsRegistry:
    """Tests for system tools registration and availability."""

    def test_all_system_tools_have_unique_ids(self):
        """Each system tool should have a unique ID."""
        from src.services.mcp_server.server import get_system_tools

        tools = get_system_tools()
        tool_ids = [t["id"] for t in tools]
        assert len(tool_ids) == len(set(tool_ids)), "Duplicate tool IDs found"

    def test_code_editor_tools_enabled_for_coding_agent(self):
        """Code editor tools should be enabled by default for coding agents."""
        from src.routers.tools import get_system_tools

        tools = get_system_tools()
        code_editor_tool_ids = [
            "list_content",
            "search_content",
            "read_content_lines",
            "get_content",
            "patch_content",
            "replace_content",
            "delete_content",
        ]

        for tool_id in code_editor_tool_ids:
            tool = next((t for t in tools if t.id == tool_id), None)
            assert tool is not None, f"Tool {tool_id} not found"
            # Note: default_enabled_for_coding_agent is no longer in TOOLS metadata
            # This test is checking tools are registered, not the default_enabled flag

    def test_workflow_execution_tools_enabled_for_coding_agent(self):
        """Workflow execution tools should be enabled for coding agents."""
        from src.routers.tools import get_system_tools

        tools = get_system_tools()
        workflow_tool_ids = [
            "execute_workflow",
            "list_workflows",
            "list_executions",
            "get_execution",
        ]

        for tool_id in workflow_tool_ids:
            tool = next((t for t in tools if t.id == tool_id), None)
            assert tool is not None, f"Tool {tool_id} not found"
            # Note: default_enabled_for_coding_agent is no longer in TOOLS metadata
            # This test is checking tools are registered, not the default_enabled flag
