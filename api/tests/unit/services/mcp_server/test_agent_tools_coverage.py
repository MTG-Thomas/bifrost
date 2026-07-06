from contextlib import asynccontextmanager
from datetime import datetime, timezone
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


def _context_without_org(*, admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        is_platform_admin=admin,
        org_id=None,
        user_id=uuid4(),
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


def _agent_detail(**overrides):
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    row = SimpleNamespace(
        id=uuid4(),
        name="Dispatcher",
        description="Routes tickets",
        system_prompt="Route work carefully",
        channels=["chat", "teams"],
        access_level=SimpleNamespace(value="role_based"),
        organization_id=uuid4(),
        is_active=True,
        created_by="creator@example.com",
        created_at=now,
        updated_at=now,
        tools=[SimpleNamespace(id=uuid4())],
        delegated_agents=[SimpleNamespace(id=uuid4())],
        roles=[SimpleNamespace(id=uuid4())],
        knowledge_sources=["kb"],
        system_tools=["list_agents"],
        llm_model="gpt-4o",
        llm_max_tokens=2048,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


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


class TestGetAgentTool:
    @pytest.mark.asyncio
    async def test_get_agent_by_id_shapes_full_agent_details(self):
        agent = _agent_detail()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_Result(agent))

        with patch.object(agents, "get_tool_db", _fake_tool_db(db)):
            result = await agents.get_agent(_context(), agent_id=str(agent.id))

        content = result.structured_content
        assert content["id"] == str(agent.id)
        assert content["name"] == "Dispatcher"
        assert content["created_at"] == agent.created_at.isoformat()
        assert content["updated_at"] == agent.updated_at.isoformat()
        assert content["tool_ids"] == [str(agent.tools[0].id)]
        assert content["delegated_agent_ids"] == [str(agent.delegated_agents[0].id)]
        assert content["role_ids"] == [str(agent.roles[0].id)]
        assert content["knowledge_sources"] == ["kb"]
        assert content["system_tools"] == ["list_agents"]
        assert content["llm_max_tokens"] == 2048
        db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_agent_by_name_returns_prioritized_lookup_result(self):
        agent = _agent_detail(
            access_level=None,
            organization_id=None,
            created_at=None,
            updated_at=None,
            tools=[],
            delegated_agents=[],
            roles=[],
            knowledge_sources=None,
            system_tools=None,
        )
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_Result(agent))

        with patch.object(agents, "get_tool_db", _fake_tool_db(db)):
            result = await agents.get_agent(_context(admin=False), agent_name="Dispatcher")

        assert result.structured_content["access_level"] == "role_based"
        assert result.structured_content["organization_id"] is None
        assert result.structured_content["created_at"] is None
        assert result.structured_content["tool_ids"] == []
        assert result.structured_content["knowledge_sources"] == []

    @pytest.mark.asyncio
    async def test_get_agent_reports_missing_invalid_and_db_errors(self):
        result = await agents.get_agent(_context())
        assert "Either agent_id or agent_name is required" in result.structured_content["error"]

        db = AsyncMock()
        with patch.object(agents, "get_tool_db", _fake_tool_db(db)):
            result = await agents.get_agent(_context(), agent_id="bad")
        assert "not a valid UUID" in result.structured_content["error"]

        db.execute = AsyncMock(return_value=_Result(None))
        with patch.object(agents, "get_tool_db", _fake_tool_db(db)):
            result = await agents.get_agent(_context(), agent_id=str(uuid4()))
        assert "not found" in result.structured_content["error"]

        db.execute = AsyncMock(side_effect=RuntimeError("query failed"))
        with patch.object(agents, "get_tool_db", _fake_tool_db(db)):
            result = await agents.get_agent(_context(), agent_id=str(uuid4()))
        assert "Error getting agent" in result.structured_content["error"]
        assert "query failed" in result.structured_content["error"]


class TestAgentMutationValidation:
    @pytest.mark.asyncio
    async def test_create_agent_rejects_invalid_inputs_before_db_access(self):
        ctx = _context(admin=False)

        result = await agents.create_agent(ctx, name="", system_prompt="prompt")
        assert "name is required" in result.structured_content["error"]

        result = await agents.create_agent(ctx, name="Dispatcher", system_prompt="")
        assert "system_prompt is required" in result.structured_content["error"]

        result = await agents.create_agent(ctx, name="x" * 256, system_prompt="prompt")
        assert "255 characters" in result.structured_content["error"]

        result = await agents.create_agent(
            ctx,
            name="Dispatcher",
            system_prompt="x" * 50001,
        )
        assert "50000 characters" in result.structured_content["error"]

        result = await agents.create_agent(
            ctx,
            name="Dispatcher",
            system_prompt="prompt",
            scope="tenant",
        )
        assert "scope must be" in result.structured_content["error"]

        result = await agents.create_agent(
            ctx,
            name="Dispatcher",
            system_prompt="prompt",
            channels=["chat", "pager"],
        )
        assert "Invalid channels" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_create_agent_rejects_privilege_and_org_scope_escalation(self):
        org_id = uuid4()
        ctx = _context(admin=False, org_id=org_id)

        result = await agents.create_agent(
            ctx,
            name="Global",
            system_prompt="prompt",
            scope="global",
        )
        assert "Only platform admins" in result.structured_content["error"]

        result = await agents.create_agent(
            ctx,
            name="Privileged",
            system_prompt="prompt",
            system_tools=["create_agent"],
        )
        assert "privileged agent management tools" in result.structured_content["error"]

        result = await agents.create_agent(
            ctx,
            name="Other Org",
            system_prompt="prompt",
            organization_id=str(uuid4()),
        )
        assert "another organization" in result.structured_content["error"]

        result = await agents.create_agent(
            _context_without_org(admin=False),
            name="No Org",
            system_prompt="prompt",
        )
        assert "organization_id is required" in result.structured_content["error"]

        result = await agents.create_agent(
            _context(admin=True, org_id=None),
            name="Bad Org",
            system_prompt="prompt",
            organization_id="not-a-uuid",
        )
        assert "not a valid UUID" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_update_agent_rejects_invalid_inputs_before_db_access(self):
        ctx = _context(admin=False)

        result = await agents.update_agent(ctx, agent_id="")
        assert "agent_id is required" in result.structured_content["error"]

        result = await agents.update_agent(ctx, agent_id="bad")
        assert "not a valid UUID" in result.structured_content["error"]

        result = await agents.update_agent(
            ctx,
            agent_id=str(uuid4()),
            channels=["chat", "pager"],
        )
        assert "Invalid channels" in result.structured_content["error"]

        result = await agents.update_agent(
            ctx,
            agent_id=str(uuid4()),
            delegated_agent_ids=[str(uuid4())],
        )
        assert "delegation" in result.structured_content["error"]

        result = await agents.update_agent(
            _context_without_org(admin=False),
            agent_id=str(uuid4()),
            name="Renamed",
        )
        assert "Organization context is required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_delete_agent_rejects_missing_or_invalid_id_before_db_access(self):
        ctx = _context(admin=False)

        result = await agents.delete_agent(ctx, agent_id="")
        assert "agent_id is required" in result.structured_content["error"]

        result = await agents.delete_agent(ctx, agent_id="bad")
        assert "not a valid UUID" in result.structured_content["error"]


class TestCreateAgentTool:
    @pytest.mark.asyncio
    async def test_create_agent_resolves_tools_delegates_and_shapes_response(self):
        org_id = uuid4()
        workflow_id = uuid4()
        delegate_id = uuid4()
        reloaded = _agent_detail(
            name="Dispatcher",
            description="Routes tickets",
            channels=["chat"],
        )
        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _Result(SimpleNamespace(id=workflow_id, organization_id=org_id)),
                _Result(SimpleNamespace(id=delegate_id, organization_id=org_id)),
                _Result(reloaded),
            ]
        )

        with patch.object(agents, "get_tool_db", _fake_tool_db(db)):
            result = await agents.create_agent(
                _context(admin=True, org_id=org_id),
                name="Dispatcher",
                system_prompt="Route work carefully",
                description="Routes tickets",
                tool_ids=[str(workflow_id)],
                delegated_agent_ids=[str(delegate_id)],
                knowledge_sources=["kb"],
                system_tools=["list_agents"],
                llm_model="gpt-4o",
                llm_max_tokens=1024,
            )

        assert result.structured_content == {
            "success": True,
            "id": str(reloaded.id),
            "name": "Dispatcher",
            "description": "Routes tickets",
            "channels": ["chat"],
            "tool_count": 1,
            "delegated_agent_count": 1,
        }
        assert db.execute.await_count == 3
        assert db.add.call_count == 3
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_agent_rejects_cross_org_tool_before_relationship_writes(self):
        org_id = uuid4()
        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock(
            return_value=_Result(SimpleNamespace(id=uuid4(), organization_id=uuid4()))
        )

        with patch.object(agents, "get_tool_db", _fake_tool_db(db)):
            result = await agents.create_agent(
                _context(admin=True, org_id=org_id),
                name="Dispatcher",
                system_prompt="Route work carefully",
                tool_ids=[str(uuid4())],
            )

        assert "belongs to a different organization" in result.structured_content["error"]
        db.flush.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_agent_returns_error_when_db_write_fails(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock(side_effect=RuntimeError("flush failed"))

        with patch.object(agents, "get_tool_db", _fake_tool_db(db)):
            result = await agents.create_agent(
                _context(admin=True, org_id=None),
                name="Global",
                system_prompt="Route work carefully",
                scope="global",
            )

        assert "Error creating agent" in result.structured_content["error"]
        assert "flush failed" in result.structured_content["error"]


class TestUpdateAgentTool:
    @pytest.mark.asyncio
    async def test_update_agent_applies_fields_relationships_and_shapes_response(self):
        org_id = uuid4()
        agent_id = uuid4()
        workflow_id = uuid4()
        delegate_id = uuid4()
        existing = _agent_detail(id=agent_id, organization_id=org_id)
        reloaded = _agent_detail(id=agent_id, name="Renamed", organization_id=org_id)
        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _Result(existing),
                _Result(SimpleNamespace(id=workflow_id, organization_id=org_id)),
                MagicMock(),
                _Result(SimpleNamespace(id=delegate_id, organization_id=org_id)),
                MagicMock(),
                _Result(reloaded),
            ]
        )

        with (
            patch.object(agents, "get_tool_db", _fake_tool_db(db)),
            patch("src.services.solutions.guard.is_solution_managed", return_value=False),
        ):
            result = await agents.update_agent(
                _context(admin=True, org_id=org_id),
                agent_id=str(agent_id),
                name="Renamed",
                description="Updated",
                system_prompt="Updated prompt",
                channels=["chat", "slack"],
                is_active=False,
                tool_ids=[str(workflow_id)],
                delegated_agent_ids=[str(delegate_id)],
                llm_model="gpt-4.1",
                llm_max_tokens=4096,
            )

        assert result.structured_content["success"] is True
        assert result.structured_content["id"] == str(agent_id)
        assert result.structured_content["name"] == "Renamed"
        assert result.structured_content["updates"] == [
            "name",
            "description",
            "system_prompt",
            "channels",
            "is_active",
            "llm_model",
            "llm_max_tokens",
            "tool_ids",
            "delegated_agent_ids",
        ]
        assert existing.name == "Renamed"
        assert existing.is_active is False
        assert db.execute.await_count == 6
        assert db.add.call_count == 2
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_agent_rejects_solution_managed_and_missing_updates(self):
        org_id = uuid4()
        agent_id = uuid4()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_Result(_agent_detail(id=agent_id, organization_id=org_id)))

        with (
            patch.object(agents, "get_tool_db", _fake_tool_db(db)),
            patch("src.services.solutions.guard.is_solution_managed", return_value=True),
            patch("src.services.solutions.guard.SOLUTION_MANAGED_MESSAGE", "locked"),
        ):
            result = await agents.update_agent(
                _context(admin=False, org_id=org_id),
                agent_id=str(agent_id),
                name="Renamed",
            )
        assert result.structured_content["error"] == "locked"
        db.flush.assert_not_called()

        db.execute = AsyncMock(return_value=_Result(_agent_detail(id=agent_id, organization_id=org_id)))
        with (
            patch.object(agents, "get_tool_db", _fake_tool_db(db)),
            patch("src.services.solutions.guard.is_solution_managed", return_value=False),
        ):
            result = await agents.update_agent(
                _context(admin=False, org_id=org_id),
                agent_id=str(agent_id),
            )
        assert "No updates provided" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_update_agent_reports_access_cross_scope_and_db_errors(self):
        org_id = uuid4()
        agent_id = uuid4()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_Result(None))

        with patch.object(agents, "get_tool_db", _fake_tool_db(db)):
            result = await agents.update_agent(
                _context(admin=True, org_id=None),
                agent_id=str(agent_id),
                name="Missing",
            )
        assert "not found" in result.structured_content["error"]

        db.execute = AsyncMock(return_value=_Result(_agent_detail(id=agent_id, organization_id=uuid4())))
        with (
            patch.object(agents, "get_tool_db", _fake_tool_db(db)),
            patch("src.services.solutions.guard.is_solution_managed", return_value=False),
        ):
            result = await agents.update_agent(
                _context(admin=False, org_id=org_id),
                agent_id=str(agent_id),
                name="Wrong Org",
            )
        assert "permission" in result.structured_content["error"]

        db.execute = AsyncMock(side_effect=RuntimeError("update query failed"))
        with patch.object(agents, "get_tool_db", _fake_tool_db(db)):
            result = await agents.update_agent(
                _context(admin=True, org_id=None),
                agent_id=str(agent_id),
                name="Broken",
            )
        assert "Error updating agent" in result.structured_content["error"]
        assert "update query failed" in result.structured_content["error"]


class TestDeleteAgentTool:
    @pytest.mark.asyncio
    async def test_delete_agent_soft_deletes_and_shapes_response(self):
        org_id = uuid4()
        agent = _agent_detail(organization_id=org_id)
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_Result(agent))

        with (
            patch.object(agents, "get_tool_db", _fake_tool_db(db)),
            patch("src.services.solutions.guard.is_solution_managed", return_value=False),
        ):
            result = await agents.delete_agent(
                _context(admin=False, org_id=org_id),
                agent_id=str(agent.id),
            )

        assert result.structured_content["success"] is True
        assert result.structured_content["id"] == str(agent.id)
        assert result.structured_content["message"] == "Agent 'Dispatcher' has been deactivated."
        assert agent.is_active is False
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_agent_reports_not_found_locked_access_and_db_errors(self):
        agent_id = uuid4()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_Result(None))

        with patch.object(agents, "get_tool_db", _fake_tool_db(db)):
            result = await agents.delete_agent(_context(admin=True, org_id=None), str(agent_id))
        assert "not found" in result.structured_content["error"]

        locked_agent = _agent_detail(id=agent_id)
        db.execute = AsyncMock(return_value=_Result(locked_agent))
        with (
            patch.object(agents, "get_tool_db", _fake_tool_db(db)),
            patch("src.services.solutions.guard.is_solution_managed", return_value=True),
            patch("src.services.solutions.guard.SOLUTION_MANAGED_MESSAGE", "locked"),
        ):
            result = await agents.delete_agent(_context(admin=True, org_id=None), str(agent_id))
        assert result.structured_content["error"] == "locked"

        db.execute = AsyncMock(return_value=_Result(_agent_detail(id=agent_id, organization_id=None)))
        with (
            patch.object(agents, "get_tool_db", _fake_tool_db(db)),
            patch("src.services.solutions.guard.is_solution_managed", return_value=False),
        ):
            result = await agents.delete_agent(_context(admin=False), str(agent_id))
        assert "Only platform admins" in result.structured_content["error"]

        db.execute = AsyncMock(side_effect=RuntimeError("delete query failed"))
        with patch.object(agents, "get_tool_db", _fake_tool_db(db)):
            result = await agents.delete_agent(_context(admin=True, org_id=None), str(agent_id))
        assert "Error deleting agent" in result.structured_content["error"]
        assert "delete query failed" in result.structured_content["error"]
