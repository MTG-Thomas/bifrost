from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.tools import agents


def _context(*, admin: bool = False, org_id=None, user_id=None) -> SimpleNamespace:
    return SimpleNamespace(
        is_platform_admin=admin,
        org_id=org_id if org_id is not None else uuid4(),
        user_id=user_id if user_id is not None else uuid4(),
        is_external=False,
        user_email="admin@example.com" if admin else "user@example.com",
    )


def _agent(**overrides):
    row = SimpleNamespace(
        id=uuid4(),
        name="Dispatcher",
        description="Routes tickets",
        channels=["chat"],
        is_active=True,
        llm_model="gpt-4o",
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _fake_tool_db(db):
    @asynccontextmanager
    async def fake_get_tool_db(_context):
        yield db

    return fake_get_tool_db


class TestAgentToolHelpers:
    def test_privilege_guard_allows_admin_and_blocks_non_admin_grants(self):
        assert agents._ensure_can_manage_agent_privileges(_context(admin=True)) is None

        global_result = agents._ensure_can_manage_agent_privileges(
            _context(admin=False),
            scope="global",
        )
        assert "global agents" in global_result.structured_content["error"]

        tool_result = agents._ensure_can_manage_agent_privileges(
            _context(admin=False),
            system_tools=["create_agent", "query_table"],
        )
        assert "create_agent" in tool_result.structured_content["error"]

        delegation_result = agents._ensure_can_manage_agent_privileges(
            _context(admin=False),
            delegated_agent_ids=[str(uuid4())],
        )
        assert "delegation" in delegation_result.structured_content["error"]

        knowledge_result = agents._ensure_can_manage_agent_privileges(
            _context(admin=False),
            knowledge_sources=["private"],
        )
        assert "knowledge sources" in knowledge_result.structured_content["error"]

    def test_reference_scope_allows_global_or_same_org_only(self):
        org_id = uuid4()

        assert agents._reference_in_agent_scope(None, org_id)
        assert agents._reference_in_agent_scope(org_id, org_id)
        assert not agents._reference_in_agent_scope(uuid4(), org_id)

    @pytest.mark.asyncio
    async def test_schema_tool_returns_agent_documentation(self):
        with patch(
            "src.services.mcp_server.schema_utils.models_to_markdown",
            return_value="# Generated models\n",
        ) as models_to_markdown:
            result = await agents.get_agent_schema(_context())

        assert "Agent Schema Documentation" in models_to_markdown.call_args.args[1]
        assert "Available Channels" in result.structured_content["schema"]
        assert "list_agents" in result.structured_content["schema"]


class TestListAgentsTool:
    @pytest.mark.asyncio
    async def test_admin_lists_all_agents(self):
        db = AsyncMock()
        repo = MagicMock()
        repo.list_all_in_scope = AsyncMock(return_value=[_agent()])

        with (
            patch.object(agents, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.agents.AgentRepository", return_value=repo) as repo_cls,
        ):
            result = await agents.list_agents(_context(admin=True, org_id=None))

        assert result.structured_content["count"] == 1
        assert result.structured_content["agents"][0]["name"] == "Dispatcher"
        repo.list_all_in_scope.assert_awaited_once()
        repo.list_agents.assert_not_called()
        assert repo_cls.call_args.kwargs["is_superuser"] is True

    @pytest.mark.asyncio
    async def test_org_user_lists_accessible_agents_with_uuid_coercion(self):
        db = AsyncMock()
        repo = MagicMock()
        repo.list_agents = AsyncMock(
            return_value=[_agent(name="Org Agent", llm_model=None)]
        )
        org_id = uuid4()
        user_id = uuid4()

        with (
            patch.object(agents, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.agents.AgentRepository", return_value=repo) as repo_cls,
        ):
            result = await agents.list_agents(
                _context(
                    admin=False,
                    org_id=str(org_id),
                    user_id=str(user_id),
                )
            )

        assert result.structured_content["count"] == 1
        assert result.structured_content["agents"][0]["llm_model"] is None
        repo.list_agents.assert_awaited_once_with(active_only=True)
        assert repo_cls.call_args.kwargs["org_id"] == org_id
        assert repo_cls.call_args.kwargs["user_id"] == user_id
        assert repo_cls.call_args.kwargs["is_external"] is False

    @pytest.mark.asyncio
    async def test_list_agents_returns_tool_error_on_repository_failure(self):
        db = AsyncMock()
        repo = MagicMock()
        repo.list_agents = AsyncMock(side_effect=RuntimeError("database down"))

        with (
            patch.object(agents, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.agents.AgentRepository", return_value=repo),
        ):
            result = await agents.list_agents(_context(admin=False))

        assert "Error listing agents" in result.structured_content["error"]
        assert "database down" in result.structured_content["error"]
