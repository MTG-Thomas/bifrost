from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.models.enums import AgentAccessLevel
from src.routers import agents, websocket


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _FakeDb:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        return self.results.pop(0)


def _principal(**overrides):
    values = {
        "user_id": uuid4(),
        "organization_id": uuid4(),
        "is_superuser": False,
        "is_platform_admin": False,
        "role_ids": [],
        "role_names": [],
        "embed": False,
        "jti": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _workflow(**overrides):
    values = {
        "id": uuid4(),
        "name": "Tool",
        "type": "tool",
        "is_active": True,
        "organization_id": None,
        "access_level": "everyone",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _State:
    pass


class _FakeWebSocket:
    def __init__(self):
        self.state = _State()
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


class TestAgentRouterCoverage:
    @pytest.mark.asyncio
    async def test_validate_agent_references_collects_tool_and_delegate_errors(self):
        target_org_id = uuid4()
        agent_id = uuid4()
        delegate_id = uuid4()
        inactive_tool = _workflow(is_active=False)
        wrong_type = _workflow(type="workflow")
        foreign_tool = _workflow(organization_id=uuid4())
        inactive_delegate = SimpleNamespace(is_active=False, organization_id=target_org_id)
        foreign_delegate = SimpleNamespace(is_active=True, organization_id=uuid4())
        db = _FakeDb(
            [
                _ScalarResult(None),
                _ScalarResult(inactive_tool),
                _ScalarResult(wrong_type),
                _ScalarResult(foreign_tool),
                _ScalarResult(inactive_delegate),
                _ScalarResult(foreign_delegate),
            ]
        )

        with pytest.raises(HTTPException) as exc:
            await agents._validate_agent_references(
                db,
                tool_ids=[
                    str(uuid4()),
                    str(inactive_tool.id),
                    str(wrong_type.id),
                    str(foreign_tool.id),
                    "not-a-uuid",
                ],
                delegated_agent_ids=[
                    str(agent_id),
                    str(delegate_id),
                    str(uuid4()),
                    "bad-delegate",
                ],
                agent_id=agent_id,
                target_org_id=target_org_id,
            )

        assert exc.value.status_code == 422
        errors = exc.value.detail["errors"]
        assert any("does not reference an existing workflow" in e for e in errors)
        assert any("inactive workflow" in e for e in errors)
        assert any("not a tool" in e for e in errors)
        assert any("different organization" in e for e in errors)
        assert any("is not a valid UUID" in e for e in errors)
        assert any("cannot delegate to itself" in e for e in errors)
        assert any("inactive agent" in e for e in errors)

    @pytest.mark.asyncio
    async def test_validate_user_tool_access_handles_everyone_authenticated_and_roles(self):
        user_id = uuid4()
        role_id = uuid4()
        everyone = _workflow(access_level="everyone")
        authenticated = _workflow(access_level="authenticated")
        restricted = _workflow(access_level="role_based")
        db = _FakeDb(
            [
                _ScalarResult([role_id]),
                _ScalarResult(everyone),
                _ScalarResult(authenticated),
                _ScalarResult(restricted),
                _ScalarResult([role_id]),
            ]
        )

        await agents._validate_user_tool_access(
            db,
            user_id,
            [str(everyone.id), str(authenticated.id), str(restricted.id)],
        )

        assert len(db.executed) == 5

    @pytest.mark.asyncio
    async def test_validate_user_tool_access_denies_external_authenticated_without_role(self):
        user_id = uuid4()
        tool = _workflow(access_level="authenticated")
        db = _FakeDb([_ScalarResult([]), _ScalarResult(tool), _ScalarResult([])])

        with pytest.raises(HTTPException) as exc:
            await agents._validate_user_tool_access(
                db,
                user_id,
                [str(tool.id)],
                is_external=True,
            )

        assert exc.value.status_code == 403
        assert "role access" in exc.value.detail

    def test_logo_data_url_and_agent_to_public_filters_relationships(self, monkeypatch):
        monkeypatch.setattr(agents, "get_system_tool_ids", lambda: ["search", "table"])
        tool_id = uuid4()
        delegate_id = uuid4()
        role_id = uuid4()
        connection_a = uuid4()
        connection_b = uuid4()
        agent = SimpleNamespace(
            id=uuid4(),
            name="Agent",
            description="Desc",
            system_prompt="Prompt",
            channels=["chat"],
            access_level=AgentAccessLevel.PRIVATE,
            organization_id=uuid4(),
            is_active=True,
            created_by="owner@example.test",
            created_at=None,
            updated_at=None,
            owner_user_id=uuid4(),
            owner=SimpleNamespace(email="owner@example.test"),
            tools=[SimpleNamespace(id=tool_id)],
            delegated_agents=[SimpleNamespace(id=delegate_id)],
            roles=[SimpleNamespace(id=role_id)],
            knowledge_sources=None,
            system_tools=["search", "unknown"],
            mcp_connections=[SimpleNamespace(id=connection_b), SimpleNamespace(id=connection_a)],
            llm_model="gpt-test",
            llm_max_tokens=100,
            max_iterations=3,
            max_token_budget=500,
            logo_data=b"logo",
            logo_content_type="image/png",
            solution_id=uuid4(),
        )

        public = agents._agent_to_public(agent)

        assert public.logo == "data:image/png;base64,bG9nbw=="
        assert public.owner_email == "owner@example.test"
        assert public.tool_ids == [str(tool_id)]
        assert public.delegated_agent_ids == [str(delegate_id)]
        assert public.role_ids == [str(role_id)]
        assert public.system_tools == ["search"]
        assert public.mcp_connection_ids == sorted([str(connection_a), str(connection_b)])
        assert public.is_solution_managed is True
        assert agents._logo_data_url(None, "image/png") is None


class TestWebSocketRouterCoverage:
    def test_file_channel_scope_and_path_helpers_cover_edge_cases(self):
        org_id = UUID("11111111-1111-1111-1111-111111111111")
        user = _principal(organization_id=org_id)
        admin = _principal(organization_id=org_id, is_platform_admin=True)

        assert websocket._parse_file_channel("files:workspace:docs") == ("workspace", "docs")
        assert websocket._parse_file_channel("bad:workspace:docs") is None
        assert websocket._parse_file_channel("files:workspace") is None
        assert websocket._path_matches("", "anything/here")
        assert websocket._path_matches("/docs", "/docs/readme.md")
        assert not websocket._path_matches("docs", "other/readme.md")
        assert websocket._file_org_and_scope(
            user=user,
            location="workspace",
            requested_scope=None,
        ) == (None, None)
        assert websocket._file_org_and_scope(
            user=admin,
            location="repo",
            requested_scope="global",
        ) == (None, "global")
        assert websocket._file_org_and_scope(
            user=user,
            location="repo",
            requested_scope="global",
        ) is None
        assert websocket._file_org_and_scope(
            user=user,
            location="repo",
            requested_scope=str(uuid4()),
        ) is None
        assert websocket._file_channel("repo", None) == "files:repo:GLOBAL"

    @pytest.mark.asyncio
    async def test_handle_file_message_revokes_policyless_subscriptions(self, monkeypatch):
        ws = _FakeWebSocket()
        ws.state.file_subscriptions = {
            "files:repo:GLOBAL:docs": {
                "channel_name": "files:repo:GLOBAL",
                "requested_channel": "files:repo:docs",
                "location": "repo",
                "scope": None,
                "organization_id": None,
                "prefix": "docs",
            }
        }
        monkeypatch.setattr(websocket, "_file_has_applicable_policy", AsyncMock(return_value=False))

        await websocket._handle_file_message(
            ws,
            _principal(),
            "files:repo:GLOBAL",
            {"type": "file_policy_changed"},
        )

        assert ws.sent == [
            {"type": "subscription_revoked", "channel": "files:repo:docs"}
        ]
        assert ws.state.file_subscriptions == {}

    @pytest.mark.asyncio
    async def test_handle_file_message_deduplicates_matching_prefixes_and_checks_access(self, monkeypatch):
        ws = _FakeWebSocket()
        ws.state.file_subscriptions = {
            "one": {
                "channel_name": "files:repo:GLOBAL",
                "requested_channel": "files:repo:docs",
                "location": "repo",
                "scope": None,
                "organization_id": None,
                "prefix": "docs",
            },
            "duplicate": {
                "channel_name": "files:repo:GLOBAL",
                "requested_channel": "files:repo:docs/nested",
                "location": "repo",
                "scope": None,
                "organization_id": None,
                "prefix": "docs",
            },
            "other": {
                "channel_name": "files:repo:GLOBAL",
                "requested_channel": "files:repo:other",
                "location": "repo",
                "scope": None,
                "organization_id": None,
                "prefix": "other",
            },
        }
        allowed = AsyncMock(return_value=True)
        monkeypatch.setattr(websocket, "_file_allowed", allowed)

        await websocket._handle_file_message(
            ws,
            _principal(),
            "files:repo:GLOBAL",
            {
                "type": "file_change",
                "location": "repo",
                "scope": "GLOBAL",
                "path": "/docs/readme.md",
                "action": "updated",
            },
        )

        assert allowed.await_count == 1
        assert ws.sent == [
            {
                "type": "file_change",
                "channel": "files:repo:docs",
                "location": "repo",
                "scope": "GLOBAL",
                "path": "docs/readme.md",
                "action": "updated",
            }
        ]

    @pytest.mark.asyncio
    async def test_re_evaluate_subscription_revokes_when_policy_missing(self, monkeypatch):
        table_id = str(uuid4())
        ws = _FakeWebSocket()
        ws.state.table_subscriptions = {
            table_id: {"filter": None, "channel_name": f"table:{table_id}"}
        }
        monkeypatch.setattr(websocket, "_load_policies_for_table", AsyncMock(return_value=None))

        await websocket._re_evaluate_subscription(ws, _principal(), table_id)

        assert ws.sent == [
            {"type": "subscription_revoked", "channel": f"table:{table_id}"}
        ]
        assert ws.state.table_subscriptions == {}
