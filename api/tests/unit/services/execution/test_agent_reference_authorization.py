from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.models.orm.workflows import Workflow
from src.services.execution.agent_helpers import resolve_agent_tools


async def _make_org(db: AsyncSession, name_prefix: str) -> Organization:
    org = Organization(
        id=uuid4(),
        name=f"{name_prefix}-{uuid4().hex[:8]}",
        is_active=True,
        created_by="test@example.com",
    )
    db.add(org)
    await db.flush()
    return org


async def _make_user(db: AsyncSession, org: Organization) -> User:
    user = User(
        id=uuid4(),
        email=f"agent-auth-{uuid4().hex[:8]}@example.com",
        name="Agent Auth User",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        is_registered=True,
        organization_id=org.id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.flush()
    return user


async def _make_tool(db: AsyncSession, org: Organization, name: str) -> Workflow:
    workflow = Workflow(
        id=uuid4(),
        name=name,
        function_name=name,
        description=f"{name} description",
        category="General",
        type="tool",
        organization_id=org.id,
        path=f"workflows/{name}.py",
        parameters_schema=[],
        tags=[],
        is_active=True,
        access_level="authenticated",
    )
    db.add(workflow)
    await db.flush()
    return workflow


async def _make_agent(
    db: AsyncSession,
    org: Organization,
    name: str,
) -> Agent:
    agent = Agent(
        id=uuid4(),
        name=name,
        description=f"{name} description",
        system_prompt="You are a test agent.",
        channels=["chat"],
        access_level=AgentAccessLevel.AUTHENTICATED,
        organization_id=org.id,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(agent)
    await db.flush()
    return agent


@pytest.mark.asyncio
async def test_resolve_agent_tools_hides_cross_org_workflow_tools_for_tenant_user(
    db_session: AsyncSession,
):
    org_a = await _make_org(db_session, "org-a")
    org_b = await _make_org(db_session, "org-b")
    user_a = await _make_user(db_session, org_a)
    tool_a = await _make_tool(db_session, org_a, "tenant_a_tool")
    tool_b = await _make_tool(db_session, org_b, "tenant_b_secret_tool")
    agent = await _make_agent(db_session, org_a, "Tenant A Agent")
    agent.tools = [tool_a, tool_b]
    await db_session.flush()

    tools, id_map = await resolve_agent_tools(
        agent,
        db_session,
        caller_user_id=user_a.id,
    )

    names = {tool.name for tool in tools}
    assert "wf_tenant_a_tool" in names
    assert "wf_tenant_b_secret_tool" not in names
    assert id_map == {"wf_tenant_a_tool": tool_a.id}


@pytest.mark.asyncio
async def test_resolve_agent_tools_hides_cross_org_delegates_for_tenant_user(
    db_session: AsyncSession,
):
    org_a = await _make_org(db_session, "org-a")
    org_b = await _make_org(db_session, "org-b")
    user_a = await _make_user(db_session, org_a)
    delegate_a = await _make_agent(db_session, org_a, "Tenant A Delegate")
    delegate_b = await _make_agent(db_session, org_b, "Tenant B Delegate")
    agent = await _make_agent(db_session, org_a, "Tenant A Parent")
    agent.delegated_agents = [delegate_a, delegate_b]
    await db_session.flush()

    tools, _ = await resolve_agent_tools(
        agent,
        db_session,
        caller_user_id=user_a.id,
    )

    names = {tool.name for tool in tools}
    assert "delegate_to_tenant_a_delegate" in names
    assert "delegate_to_tenant_b_delegate" not in names


@pytest.mark.asyncio
async def test_resolve_agent_tools_keeps_cross_org_references_for_platform_admin(
    db_session: AsyncSession,
):
    org_a = await _make_org(db_session, "org-a")
    org_b = await _make_org(db_session, "org-b")
    tool_b = await _make_tool(db_session, org_b, "tenant_b_secret_tool")
    delegate_b = await _make_agent(db_session, org_b, "Tenant B Delegate")
    agent = await _make_agent(db_session, org_a, "Tenant A Parent")
    agent.tools = [tool_b]
    agent.delegated_agents = [delegate_b]
    await db_session.flush()

    tools, id_map = await resolve_agent_tools(
        agent,
        db_session,
        caller_is_platform_admin=True,
    )

    names = {tool.name for tool in tools}
    assert "wf_tenant_b_secret_tool" in names
    assert "delegate_to_tenant_b_delegate" in names
    assert id_map == {"wf_tenant_b_secret_tool": tool_b.id}
