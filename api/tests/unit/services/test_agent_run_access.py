from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.auth import UserPrincipal
from src.models.enums import AgentAccessLevel
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.services import agent_run_access


def _principal(
    *,
    org_id=None,
    is_superuser: bool = False,
) -> UserPrincipal:
    user_id = uuid4()
    return UserPrincipal(
        user_id=user_id,
        email=f"{user_id}@example.test",
        organization_id=org_id,
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
        roles=[],
    )


def _sql(query) -> str:
    return str(query.compile(compile_kwargs={"literal_binds": True}))


def _sql_uuid(value) -> str:
    return str(value).replace("-", "")


class TestAgentAccessConditions:
    def test_global_superuser_has_no_agent_access_filters(self) -> None:
        user = _principal(org_id=None, is_superuser=True)

        assert agent_run_access.agent_access_conditions(user) == []

    def test_user_without_org_matches_false_condition(self) -> None:
        user = _principal(org_id=None)

        compiled = _sql(select(Agent).where(*agent_run_access.agent_access_conditions(user)))

        assert "false" in compiled.lower()

    def test_org_superuser_has_platform_wide_agent_access(self) -> None:
        org_id = uuid4()
        user = _principal(org_id=org_id, is_superuser=True)

        assert agent_run_access.agent_access_conditions(user) == []

    def test_org_user_gets_scope_and_visibility_filters(self) -> None:
        org_id = uuid4()
        user = _principal(org_id=org_id)

        compiled = _sql(select(Agent).where(*agent_run_access.agent_access_conditions(user)))

        assert _sql_uuid(org_id) in compiled
        assert AgentAccessLevel.AUTHENTICATED.value in compiled
        assert AgentAccessLevel.PRIVATE.value in compiled
        assert AgentAccessLevel.ROLE_BASED.value in compiled
        assert "user_roles" in compiled
        assert _sql_uuid(user.user_id) in compiled


class TestApplyAgentRunAccess:
    def test_global_superuser_query_only_joins_agents(self) -> None:
        user = _principal(org_id=None, is_superuser=True)

        compiled = _sql(agent_run_access.apply_agent_run_access(select(AgentRun), user))

        assert "JOIN agents ON agent_runs.agent_id = agents.id" in compiled
        assert "WHERE" not in compiled

    def test_org_superuser_query_only_joins_agents(self) -> None:
        user = _principal(org_id=uuid4(), is_superuser=True)

        compiled = _sql(agent_run_access.apply_agent_run_access(select(AgentRun), user))

        assert "JOIN agents ON agent_runs.agent_id = agents.id" in compiled
        assert "WHERE" not in compiled

    def test_user_without_org_query_is_denied(self) -> None:
        user = _principal(org_id=None)

        compiled = _sql(agent_run_access.apply_agent_run_access(select(AgentRun), user))

        assert "JOIN agents ON agent_runs.agent_id = agents.id" in compiled
        assert "false" in compiled.lower()

    def test_org_user_query_filters_run_org_and_agent_access(self) -> None:
        org_id = uuid4()
        user = _principal(org_id=org_id)

        compiled = _sql(agent_run_access.apply_agent_run_access(select(AgentRun), user))

        assert "agent_runs.org_id" in compiled
        assert _sql_uuid(org_id) in compiled
        assert AgentAccessLevel.AUTHENTICATED.value in compiled


@pytest.mark.asyncio
class TestLoadHelpers:
    async def test_load_agent_for_user_executes_scoped_agent_query(self) -> None:
        returned_agent = object()
        db = _FakeDb(returned_agent)
        agent_id = uuid4()

        assert await agent_run_access.load_agent_for_user(
            db, agent_id, _principal(org_id=uuid4())
        ) is returned_agent

        compiled = _sql(db.executed[0])
        assert _sql_uuid(agent_id) in compiled
        assert "agents.id" in compiled

    async def test_load_agent_by_name_prefers_caller_org_when_present(self) -> None:
        returned_agent = object()
        db = _FakeDb(returned_agent, scalars_first=True)
        org_id = uuid4()

        assert await agent_run_access.load_agent_by_name_for_user(
            db, "Support", _principal(org_id=org_id)
        ) is returned_agent

        compiled = _sql(db.executed[0])
        assert "lower(agents.name) LIKE lower" in compiled
        assert "ORDER BY agents.organization_id" in compiled

    async def test_load_agent_by_name_omits_org_order_for_global_principal(self) -> None:
        returned_agent = object()
        db = _FakeDb(returned_agent, scalars_first=True)

        assert await agent_run_access.load_agent_by_name_for_user(
            db, "Support", _principal(org_id=None, is_superuser=True)
        ) is returned_agent

        assert "ORDER BY" not in _sql(db.executed[0])

    async def test_load_agent_run_for_user_applies_options_and_access(self) -> None:
        returned_run = object()
        option = selectinload(AgentRun.steps)
        db = _FakeDb(returned_run)
        run_id = uuid4()

        assert await agent_run_access.load_agent_run_for_user(
            db,
            run_id,
            _principal(org_id=uuid4()),
            options=[option],
        ) is returned_run

        compiled = _sql(db.executed[0])
        assert _sql_uuid(run_id) in compiled
        assert "JOIN agents ON agent_runs.agent_id = agents.id" in compiled


class _FakeResult:
    def __init__(self, value: object, *, scalars_first: bool = False) -> None:
        self.value = value
        self.scalars_first = scalars_first

    def scalar_one_or_none(self) -> object:
        return self.value

    def scalars(self) -> SimpleNamespace:
        assert self.scalars_first
        return SimpleNamespace(first=lambda: self.value)


class _FakeDb:
    def __init__(self, value: object, *, scalars_first: bool = False) -> None:
        self.value = value
        self.scalars_first = scalars_first
        self.executed = []

    async def execute(self, query):
        self.executed.append(query)
        return _FakeResult(self.value, scalars_first=self.scalars_first)
