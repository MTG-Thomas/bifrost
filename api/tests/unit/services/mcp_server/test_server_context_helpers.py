"""Focused unit tests for MCP server context/runtime helper branches."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.mcp_server import agent_scope, server


class FakeToolError(Exception):
    """Stand-in for fastmcp.exceptions.ToolError in isolated unit tests."""


class FakeSQLAlchemyError(Exception):
    """Stand-in for sqlalchemy.exc.SQLAlchemyError in isolated unit tests."""


def test_get_scoped_agent_id_returns_uuid(monkeypatch):
    agent_id = uuid4()

    monkeypatch.setattr(
        agent_scope,
        "get_http_request",
        lambda: SimpleNamespace(scope={"mcp_agent_id": str(agent_id)}),
    )

    assert agent_scope.get_scoped_agent_id() == agent_id


@pytest.mark.parametrize(
    "request_factory",
    [
        lambda: (_ for _ in ()).throw(RuntimeError("no request")),
        lambda: SimpleNamespace(scope={"mcp_agent_id": "not-a-uuid"}),
        lambda: SimpleNamespace(scope=None),
    ],
)
def test_get_scoped_agent_id_handles_missing_or_invalid_request(
    monkeypatch,
    request_factory,
):
    monkeypatch.setattr(agent_scope, "get_http_request", request_factory)

    assert agent_scope.get_scoped_agent_id() is None


def test_has_http_request_context(monkeypatch):
    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_http_request",
        lambda: SimpleNamespace(scope={}),
    )
    assert server._has_http_request_context() is True

    def raise_no_context():
        raise RuntimeError("not in HTTP context")

    monkeypatch.setattr("fastmcp.server.dependencies.get_http_request", raise_no_context)
    assert server._has_http_request_context() is False


def test_get_context_from_token_requires_authentication(monkeypatch):
    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: None)
    monkeypatch.setattr("fastmcp.exceptions.ToolError", FakeToolError)

    with pytest.raises(FakeToolError, match="Authentication required"):
        server._get_context_from_token()


def test_get_context_from_token_normalizes_claims(monkeypatch):
    user_id = uuid4()
    org_id = uuid4()
    token = SimpleNamespace(
        claims={
            "user_id": str(user_id),
            "org_id": str(org_id),
            "is_superuser": True,
            "is_external": True,
            "email": "agent@example.com",
            "name": "Agent User",
        }
    )

    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: token)

    context = server._get_context_from_token()

    assert context.user_id == user_id
    assert context.org_id == org_id
    assert context.is_platform_admin is True
    assert context.is_external is True
    assert context.user_email == "agent@example.com"
    assert context.user_name == "Agent User"


@pytest.mark.asyncio
async def test_get_runtime_context_uses_agent_scoped_namespaces(monkeypatch):
    user_id = uuid4()
    org_id = uuid4()
    agent_id = uuid4()
    token = SimpleNamespace(
        claims={
            "roles": ["member"],
            "is_superuser": False,
            "is_external": True,
            "user_id": str(user_id),
            "org_id": str(org_id),
            "email": "external@example.com",
            "name": "External User",
        }
    )
    service_calls = []

    class FakeService:
        def __init__(self, db):
            self.db = db

        async def get_tools_for_agent(self, **kwargs):
            service_calls.append(("agent", kwargs))
            return SimpleNamespace(accessible_namespaces=("docs", "runbooks"))

        async def get_accessible_tools(self, **kwargs):
            service_calls.append(("union", kwargs))
            return SimpleNamespace(accessible_namespaces=("unexpected",))

    @asynccontextmanager
    async def fake_db_context():
        yield object()

    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: token)
    monkeypatch.setattr(server, "_get_agent_id_from_scope", lambda: agent_id)
    monkeypatch.setattr("src.core.database.get_db_context", lambda: fake_db_context())
    monkeypatch.setattr(
        "src.services.mcp_server.tool_access.MCPToolAccessService",
        FakeService,
    )
    monkeypatch.setattr("fastmcp.exceptions.ToolError", FakeToolError)

    context = await server._get_runtime_context()

    assert context.accessible_namespaces == ["docs", "runbooks"]
    assert context.user_id == user_id
    assert context.org_id == org_id
    assert context.is_external is True
    assert service_calls == [
        (
            "agent",
            {
                "agent_id": agent_id,
                "user_roles": ["member"],
                "is_superuser": False,
                "user_id": str(user_id),
                "org_id": str(org_id),
                "is_external": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_get_runtime_context_defers_namespaces_without_agent(monkeypatch):
    user_id = uuid4()
    token = SimpleNamespace(
        claims={
            "roles": ["admin"],
            "is_superuser": True,
            "is_external": False,
            "user_id": str(user_id),
            "org_id": None,
        }
    )
    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: token)
    monkeypatch.setattr(server, "_get_agent_id_from_scope", lambda: None)
    monkeypatch.setattr("fastmcp.exceptions.ToolError", FakeToolError)

    context = await server._get_runtime_context()

    assert context.accessible_namespaces == []
    assert context.is_platform_admin is True


@pytest.mark.asyncio
async def test_get_runtime_context_rejects_inaccessible_agent(monkeypatch):
    token = SimpleNamespace(
        claims={
            "roles": [],
            "is_superuser": False,
            "is_external": False,
            "user_id": str(uuid4()),
            "org_id": str(uuid4()),
        }
    )

    class FakeService:
        def __init__(self, db):
            self.db = db

        async def get_tools_for_agent(self, **kwargs):
            return None

    @asynccontextmanager
    async def fake_db_context():
        yield object()

    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: token)
    monkeypatch.setattr(server, "_get_agent_id_from_scope", uuid4)
    monkeypatch.setattr("src.core.database.get_db_context", lambda: fake_db_context())
    monkeypatch.setattr(
        "src.services.mcp_server.tool_access.MCPToolAccessService",
        FakeService,
    )
    monkeypatch.setattr("fastmcp.exceptions.ToolError", FakeToolError)

    with pytest.raises(FakeToolError, match="Agent not found or inaccessible"):
        await server._get_runtime_context()


@pytest.mark.asyncio
async def test_get_runtime_context_wraps_database_errors(monkeypatch):
    token = SimpleNamespace(
        claims={
            "roles": [],
            "is_superuser": False,
            "is_external": False,
            "user_id": str(uuid4()),
            "org_id": str(uuid4()),
        }
    )

    class FakeService:
        def __init__(self, db):
            self.db = db

        async def get_tools_for_agent(self, **kwargs):
            raise FakeSQLAlchemyError("db unavailable")

    @asynccontextmanager
    async def fake_db_context():
        yield object()

    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: token)
    monkeypatch.setattr(server, "_get_agent_id_from_scope", uuid4)
    monkeypatch.setattr("src.core.database.get_db_context", lambda: fake_db_context())
    monkeypatch.setattr(
        "src.services.mcp_server.tool_access.MCPToolAccessService",
        FakeService,
    )
    monkeypatch.setattr("fastmcp.exceptions.ToolError", FakeToolError)
    monkeypatch.setattr("sqlalchemy.exc.SQLAlchemyError", FakeSQLAlchemyError)

    with pytest.raises(FakeToolError, match="Failed to resolve accessible namespaces"):
        await server._get_runtime_context()


@pytest.mark.asyncio
async def test_bifrost_mcp_server_preserves_default_context():
    user_id = uuid4()
    org_id = uuid4()

    context = server.MCPContext(
        user_id=user_id,
        org_id=org_id,
        is_platform_admin=True,
        user_email="admin@example.com",
        user_name="Admin User",
    )
    mcp_server = server.BifrostMCPServer(context)

    assert mcp_server.context is context
    assert context.user_id == user_id
    assert context.org_id == org_id
    assert mcp_server.context.is_platform_admin is True
    assert mcp_server.context.user_email == "admin@example.com"
    assert mcp_server.context.user_name == "Admin User"


def test_get_system_tool_function_returns_callable_or_none():
    assert server.get_system_tool_function("list_workflows") is not None
    assert server.get_system_tool_function("definitely_missing_tool") is None


def test_get_system_tools_handles_missing_function_and_defaults(monkeypatch):
    def no_annotation(context, required, optional="default"):
        return None

    fake_module = SimpleNamespace(
        TOOLS=[
            ("no_annotation", "No Annotation", "Missing annotations"),
            ("missing_function", "Missing Function", "No callable exists"),
        ],
        no_annotation=no_annotation,
    )
    monkeypatch.setattr(server, "TOOL_MODULES", [fake_module])

    tools = server.get_system_tools()

    assert tools == [
        {
            "id": "no_annotation",
            "name": "No Annotation",
            "description": "Missing annotations",
            "parameters": {
                "type": "object",
                "properties": {
                    "required": {"type": "string"},
                    "optional": {"type": "string"},
                },
                "required": ["required"],
                "additionalProperties": False,
            },
        },
        {
            "id": "missing_function",
            "name": "Missing Function",
            "description": "No callable exists",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    ]
