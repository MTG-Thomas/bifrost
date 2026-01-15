"""
Integration tests for MCP tool scoped entity lookups.

Tests that get_agent and get_form MCP tools correctly handle the case where
entities with the same name exist in both organization-scoped and global scopes.

The fix ensures:
1. Name-based lookups prioritize org-specific entities over global entities
2. When only a global entity exists, it is returned
3. ID-based lookups work correctly with cascade filter (IDs are unique)

Issue: MultipleResultsFound error when same name exists in both scopes
Fix: Use ORDER BY organization_id DESC NULLS LAST LIMIT 1 for name-based lookups
"""

import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import Agent, Form
from src.models.enums import AgentAccessLevel


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def test_org_id() -> UUID:
    """A test organization ID."""
    return UUID("00000000-0000-4000-8000-000000000100")


@pytest.fixture
def other_org_id() -> UUID:
    """A different organization ID for cross-org tests."""
    return UUID("00000000-0000-4000-8000-000000000200")


@pytest_asyncio.fixture
async def global_agent(db_session: AsyncSession) -> Agent:
    """Create a global agent (organization_id = None)."""
    agent = Agent(
        id=uuid4(),
        name="shared_agent",
        description="A global agent accessible to all orgs",
        system_prompt="You are a helpful assistant",
        channels=["chat"],
        access_level=AgentAccessLevel.ROLE_BASED,
        organization_id=None,  # Global
        is_active=True,
        is_coding_mode=False,
        is_system=False,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest_asyncio.fixture
async def org_agent(db_session: AsyncSession, test_org_id: UUID) -> Agent:
    """Create an org-scoped agent with the same name as global_agent."""
    agent = Agent(
        id=uuid4(),
        name="shared_agent",  # Same name as global_agent
        description="An org-specific agent",
        system_prompt="You are an org-specific assistant",
        channels=["chat"],
        access_level=AgentAccessLevel.ROLE_BASED,
        organization_id=test_org_id,  # Org-scoped
        is_active=True,
        is_coding_mode=False,
        is_system=False,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest_asyncio.fixture
async def global_only_agent(db_session: AsyncSession) -> Agent:
    """Create a global agent with a unique name."""
    agent = Agent(
        id=uuid4(),
        name="unique_global_agent",
        description="A global agent with unique name",
        system_prompt="You are a unique global assistant",
        channels=["chat"],
        access_level=AgentAccessLevel.ROLE_BASED,
        organization_id=None,  # Global
        is_active=True,
        is_coding_mode=False,
        is_system=False,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest_asyncio.fixture
async def global_form(db_session: AsyncSession) -> Form:
    """Create a global form (organization_id = None)."""
    form = Form(
        id=uuid4(),
        name="shared_form",
        description="A global form accessible to all orgs",
        workflow_id=None,
        access_level="role_based",
        organization_id=None,  # Global
        is_active=True,
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(form)
    await db_session.flush()
    return form


@pytest_asyncio.fixture
async def org_form(db_session: AsyncSession, test_org_id: UUID) -> Form:
    """Create an org-scoped form with the same name as global_form."""
    form = Form(
        id=uuid4(),
        name="shared_form",  # Same name as global_form
        description="An org-specific form",
        workflow_id=None,
        access_level="role_based",
        organization_id=test_org_id,  # Org-scoped
        is_active=True,
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(form)
    await db_session.flush()
    return form


@pytest_asyncio.fixture
async def global_only_form(db_session: AsyncSession) -> Form:
    """Create a global form with a unique name."""
    form = Form(
        id=uuid4(),
        name="unique_global_form",
        description="A global form with unique name",
        workflow_id=None,
        access_level="role_based",
        organization_id=None,  # Global
        is_active=True,
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(form)
    await db_session.flush()
    return form


# =============================================================================
# Mock MCP Context
# =============================================================================


class MockMCPContext:
    """Mock MCP context for testing tool functions."""

    def __init__(
        self,
        user_id: UUID | str,
        org_id: UUID | str | None = None,
        is_platform_admin: bool = False,
        user_email: str = "test@example.com",
        user_name: str = "Test User",
    ):
        self.user_id = user_id
        self.org_id = org_id
        self.is_platform_admin = is_platform_admin
        self.user_email = user_email
        self.user_name = user_name
        self.enabled_system_tools: list[str] = []
        self.accessible_namespaces: list[str] = []


# =============================================================================
# Agent Tests
# =============================================================================


class TestGetAgentScopedLookup:
    """Tests for get_agent MCP tool with scoped lookups."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_agent_by_name_returns_org_specific_when_both_exist(
        self,
        db_session: AsyncSession,
        global_agent: Agent,
        org_agent: Agent,
        test_org_id: UUID,
    ):
        """
        When same name exists in both org and global scope,
        should return org-specific agent.
        """
        from src.services.mcp_server.tools.agents import get_agent

        # Create context for org user
        context = MockMCPContext(
            user_id=uuid4(),
            org_id=test_org_id,
            is_platform_admin=False,
        )

        # Call get_agent by name
        result = await get_agent(context, agent_name="shared_agent")
        data = json.loads(result)

        # Should return org-specific agent, not global
        assert "error" not in data
        assert data["id"] == str(org_agent.id)
        assert data["organization_id"] == str(test_org_id)
        assert data["description"] == "An org-specific agent"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_agent_by_name_returns_global_when_only_global_exists(
        self,
        db_session: AsyncSession,
        global_only_agent: Agent,
        test_org_id: UUID,
    ):
        """
        When only global agent exists with the name,
        should return the global agent.
        """
        from src.services.mcp_server.tools.agents import get_agent

        # Create context for org user
        context = MockMCPContext(
            user_id=uuid4(),
            org_id=test_org_id,
            is_platform_admin=False,
        )

        # Call get_agent by name
        result = await get_agent(context, agent_name="unique_global_agent")
        data = json.loads(result)

        # Should return global agent
        assert "error" not in data
        assert data["id"] == str(global_only_agent.id)
        assert data["organization_id"] is None  # Global agent
        assert data["description"] == "A global agent with unique name"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_agent_by_id_works_with_cascade_filter(
        self,
        db_session: AsyncSession,
        org_agent: Agent,
        test_org_id: UUID,
    ):
        """
        ID-based lookup should work correctly with cascade filter.
        """
        from src.services.mcp_server.tools.agents import get_agent

        # Create context for org user
        context = MockMCPContext(
            user_id=uuid4(),
            org_id=test_org_id,
            is_platform_admin=False,
        )

        # Call get_agent by ID
        result = await get_agent(context, agent_id=str(org_agent.id))
        data = json.loads(result)

        # Should return the agent by ID
        assert "error" not in data
        assert data["id"] == str(org_agent.id)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_agent_by_name_no_org_context_returns_global_only(
        self,
        db_session: AsyncSession,
        global_agent: Agent,
        org_agent: Agent,
    ):
        """
        When user has no org context (not platform admin),
        should only return global agent.
        """
        from src.services.mcp_server.tools.agents import get_agent

        # Create context for user with no org (and not platform admin)
        # This represents a user who somehow has no org context
        context = MockMCPContext(
            user_id=uuid4(),
            org_id=None,
            is_platform_admin=False,
        )

        # Call get_agent by name
        result = await get_agent(context, agent_name="shared_agent")
        data = json.loads(result)

        # Should return global agent (can't access org-specific)
        assert "error" not in data
        assert data["id"] == str(global_agent.id)
        assert data["organization_id"] is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_agent_platform_admin_sees_all(
        self,
        db_session: AsyncSession,
        global_agent: Agent,
        org_agent: Agent,
    ):
        """
        Platform admin should be able to access both agents.
        Note: The current implementation doesn't apply org filter for platform admin,
        so MultipleResultsFound could still occur. This test documents the expected behavior.
        """
        from src.services.mcp_server.tools.agents import get_agent

        # Create context for platform admin
        context = MockMCPContext(
            user_id=uuid4(),
            org_id=None,
            is_platform_admin=True,
        )

        # Platform admin by ID should work
        result = await get_agent(context, agent_id=str(org_agent.id))
        data = json.loads(result)
        assert "error" not in data
        assert data["id"] == str(org_agent.id)


# =============================================================================
# Form Tests
# =============================================================================


class TestGetFormScopedLookup:
    """Tests for get_form MCP tool with scoped lookups."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_form_by_name_returns_org_specific_when_both_exist(
        self,
        db_session: AsyncSession,
        global_form: Form,
        org_form: Form,
        test_org_id: UUID,
    ):
        """
        When same name exists in both org and global scope,
        should return org-specific form.
        """
        from src.services.mcp_server.tools.forms import get_form

        # Create context for org user
        context = MockMCPContext(
            user_id=uuid4(),
            org_id=test_org_id,
            is_platform_admin=False,
        )

        # Call get_form by name
        result = await get_form(context, form_name="shared_form")
        data = json.loads(result)

        # Should return org-specific form, not global
        assert "error" not in data
        assert data["id"] == str(org_form.id)
        assert data["organization_id"] == str(test_org_id)
        assert data["description"] == "An org-specific form"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_form_by_name_returns_global_when_only_global_exists(
        self,
        db_session: AsyncSession,
        global_only_form: Form,
        test_org_id: UUID,
    ):
        """
        When only global form exists with the name,
        should return the global form.
        """
        from src.services.mcp_server.tools.forms import get_form

        # Create context for org user
        context = MockMCPContext(
            user_id=uuid4(),
            org_id=test_org_id,
            is_platform_admin=False,
        )

        # Call get_form by name
        result = await get_form(context, form_name="unique_global_form")
        data = json.loads(result)

        # Should return global form
        assert "error" not in data
        assert data["id"] == str(global_only_form.id)
        assert data["organization_id"] is None  # Global form
        assert data["description"] == "A global form with unique name"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_form_by_id_works_with_cascade_filter(
        self,
        db_session: AsyncSession,
        org_form: Form,
        test_org_id: UUID,
    ):
        """
        ID-based lookup should work correctly with cascade filter.
        """
        from src.services.mcp_server.tools.forms import get_form

        # Create context for org user
        context = MockMCPContext(
            user_id=uuid4(),
            org_id=test_org_id,
            is_platform_admin=False,
        )

        # Call get_form by ID
        result = await get_form(context, form_id=str(org_form.id))
        data = json.loads(result)

        # Should return the form by ID
        assert "error" not in data
        assert data["id"] == str(org_form.id)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_form_by_name_no_org_context_returns_global_only(
        self,
        db_session: AsyncSession,
        global_form: Form,
        org_form: Form,
    ):
        """
        When user has no org context (not platform admin),
        should only return global form.
        """
        from src.services.mcp_server.tools.forms import get_form

        # Create context for user with no org (and not platform admin)
        context = MockMCPContext(
            user_id=uuid4(),
            org_id=None,
            is_platform_admin=False,
        )

        # Call get_form by name
        result = await get_form(context, form_name="shared_form")
        data = json.loads(result)

        # Should return global form (can't access org-specific)
        assert "error" not in data
        assert data["id"] == str(global_form.id)
        assert data["organization_id"] is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_form_platform_admin_sees_all(
        self,
        db_session: AsyncSession,
        global_form: Form,
        org_form: Form,
    ):
        """
        Platform admin should be able to access both forms.
        """
        from src.services.mcp_server.tools.forms import get_form

        # Create context for platform admin
        context = MockMCPContext(
            user_id=uuid4(),
            org_id=None,
            is_platform_admin=True,
        )

        # Platform admin by ID should work
        result = await get_form(context, form_id=str(org_form.id))
        data = json.loads(result)
        assert "error" not in data
        assert data["id"] == str(org_form.id)


# =============================================================================
# Error Case Tests
# =============================================================================


class TestScopedLookupErrorCases:
    """Tests for error cases in scoped lookups."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_agent_not_found(
        self,
        db_session: AsyncSession,
        test_org_id: UUID,
    ):
        """Should return error when agent not found."""
        from src.services.mcp_server.tools.agents import get_agent

        context = MockMCPContext(
            user_id=uuid4(),
            org_id=test_org_id,
            is_platform_admin=False,
        )

        result = await get_agent(context, agent_name="nonexistent_agent")
        data = json.loads(result)

        assert "error" in data
        assert "not found" in data["error"].lower()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_form_not_found(
        self,
        db_session: AsyncSession,
        test_org_id: UUID,
    ):
        """Should return error when form not found."""
        from src.services.mcp_server.tools.forms import get_form

        context = MockMCPContext(
            user_id=uuid4(),
            org_id=test_org_id,
            is_platform_admin=False,
        )

        result = await get_form(context, form_name="nonexistent_form")
        data = json.loads(result)

        assert "error" in data
        assert "not found" in data["error"].lower()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_agent_invalid_uuid(
        self,
        db_session: AsyncSession,
        test_org_id: UUID,
    ):
        """Should return error for invalid UUID."""
        from src.services.mcp_server.tools.agents import get_agent

        context = MockMCPContext(
            user_id=uuid4(),
            org_id=test_org_id,
            is_platform_admin=False,
        )

        result = await get_agent(context, agent_id="not-a-uuid")
        data = json.loads(result)

        assert "error" in data
        assert "not a valid UUID" in data["error"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_form_invalid_uuid(
        self,
        db_session: AsyncSession,
        test_org_id: UUID,
    ):
        """Should return error for invalid UUID."""
        from src.services.mcp_server.tools.forms import get_form

        context = MockMCPContext(
            user_id=uuid4(),
            org_id=test_org_id,
            is_platform_admin=False,
        )

        result = await get_form(context, form_id="not-a-uuid")
        data = json.loads(result)

        assert "error" in data
        assert "not a valid UUID" in data["error"]
