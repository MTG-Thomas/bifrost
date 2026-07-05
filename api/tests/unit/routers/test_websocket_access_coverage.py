from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.routers import websocket as ws_mod


def _user(*, org_id=None, is_superuser: bool = False, **overrides):
    user = UserPrincipal(
        user_id=uuid4(),
        email="user@example.com",
        organization_id=org_id if org_id is not None else uuid4(),
        is_superuser=is_superuser,
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _OneResult:
    def __init__(self, value):
        self._value = value

    def one_or_none(self):
        return self._value


class _Db:
    def __init__(self, result):
        self._result = result

    async def execute(self, _stmt):
        return self._result


class _WebSocket:
    def __init__(self):
        self.state = SimpleNamespace()
        self.send_json = AsyncMock()


def _db_context(db):
    @asynccontextmanager
    async def fake_get_db_context():
        yield db

    return fake_get_db_context


def test_parse_channels_accepts_strings_objects_and_rejects_invalid_specs():
    specs = ws_mod._parse_channels(
        [
            "conversation:abc",
            {
                "name": "table:customers",
                "filter": {"user": "is_platform_admin"},
                "scope": "global",
            },
            {"name": "files:workspace:docs", "scope": 42},
        ]
    )

    assert [spec.name for spec in specs] == [
        "conversation:abc",
        "table:customers",
        "files:workspace:docs",
    ]
    assert specs[1].filter is not None
    assert specs[1].scope == "global"
    assert specs[2].scope is None

    with pytest.raises(ws_mod.WSError, match="channel must be"):
        ws_mod._parse_channels([{"filter": {}}])

    with pytest.raises(ws_mod.WSError, match="invalid filter"):
        ws_mod._parse_channels([{"name": "table:bad", "filter": {"unknown": []}}])


def test_file_channel_scope_and_path_helpers_cover_access_boundaries():
    org_id = uuid4()
    user = _user(org_id=org_id)
    admin = _user(org_id=org_id, is_superuser=True)

    assert ws_mod._parse_file_channel("files:workspace:apps/demo") == (
        "workspace",
        "apps/demo",
    )
    assert ws_mod._parse_file_channel("table:abc") is None
    assert ws_mod._path_matches("apps/demo", "/apps/demo/main.py")
    assert not ws_mod._path_matches("apps/demo", "apps/other/main.py")
    assert ws_mod._file_org_and_scope(
        user=user,
        location="workspace",
        requested_scope="ignored",
    ) == (None, None)
    assert ws_mod._file_org_and_scope(
        user=user,
        location="repo",
        requested_scope=str(org_id),
    ) == (org_id, str(org_id))
    assert ws_mod._file_org_and_scope(
        user=user,
        location="repo",
        requested_scope="global",
    ) is None
    assert ws_mod._file_org_and_scope(
        user=admin,
        location="repo",
        requested_scope="global",
    ) == (None, "global")
    assert ws_mod._file_org_and_scope(
        user=user,
        location="repo",
        requested_scope="not-a-uuid",
    ) is None
    assert ws_mod._file_channel("repo", None) == "files:repo:GLOBAL"


@pytest.mark.asyncio
async def test_load_policies_for_table_handles_cache_validation_and_ref_errors():
    table_id = str(uuid4())
    row = ({"policies": [{"name": "read", "actions": ["read"]}]}, uuid4(), None)
    ws_mod._table_policy_cache.clear()

    with (
        patch.object(ws_mod, "get_db_context", _db_context(_Db(_OneResult(row)))),
        patch.object(ws_mod, "resolve_policy_refs", AsyncMock()) as resolve_refs,
    ):
        policies = await ws_mod._load_policies_for_table(table_id)

    assert policies is not None
    assert len(policies.policies) == 1
    resolve_refs.assert_awaited_once()
    assert table_id in ws_mod._table_policy_cache

    ws_mod._table_policy_cache[table_id] = ws_mod._RawTableEntry(
        access={"policies": [{"$ref": "missing"}]},
        org_id=uuid4(),
        solution_id=None,
    )
    with (
        patch.object(ws_mod, "get_db_context", _db_context(_Db(_OneResult(None)))),
        patch.object(
            ws_mod,
            "resolve_policy_refs",
            AsyncMock(side_effect=ws_mod.PolicyRuleNotFound("missing")),
        ),
    ):
        denied = await ws_mod._load_policies_for_table(table_id)

    assert denied is not None
    assert denied.policies == []

    invalid_row = ({"policies": [{"name": "bad", "actions": []}]}, None, None)
    with patch.object(ws_mod, "get_db_context", _db_context(_Db(_OneResult(invalid_row)))):
        invalid = await ws_mod._load_policies_for_table("table-name")

    assert invalid is not None
    assert invalid.policies == []


@pytest.mark.asyncio
async def test_authorize_file_subscribe_reports_errors_and_registers_success():
    user = _user()
    websocket = _WebSocket()

    assert await ws_mod._authorize_file_subscribe(
        websocket,
        user,
        ws_mod.ChannelSpec(name="bad", filter=None),
    ) is None
    websocket.send_json.assert_awaited_with(
        {"type": "error", "channel": "bad", "message": "Invalid file channel"}
    )

    websocket = _WebSocket()
    with patch.object(ws_mod, "_populate_user_roles", AsyncMock()):
        assert await ws_mod._authorize_file_subscribe(
            websocket,
            user,
            ws_mod.ChannelSpec(name="files:repo:docs", filter=None, scope="global"),
        ) is None
    websocket.send_json.assert_awaited_with(
        {
            "type": "error",
            "channel": "files:repo:docs",
            "message": "Access denied",
        }
    )

    websocket = _WebSocket()
    org_id = uuid4()
    with (
        patch.object(ws_mod, "_populate_user_roles", AsyncMock()) as populate,
        patch.object(ws_mod, "_file_has_applicable_policy", AsyncMock(return_value=True)) as has_policy,
    ):
        channel = await ws_mod._authorize_file_subscribe(
            websocket,
            _user(org_id=org_id),
            ws_mod.ChannelSpec(
                name="files:repo:docs",
                filter=None,
                scope=str(org_id),
            ),
        )

    assert channel == f"files:repo:{org_id}"
    populate.assert_awaited_once()
    has_policy.assert_awaited_once()
    assert f"files:repo:{org_id}:docs" in websocket.state.file_subscriptions
    assert hasattr(websocket, "_file_dispatcher")


@pytest.mark.asyncio
async def test_handle_file_message_revokes_filters_and_deduplicates_delivery():
    user = _user()
    websocket = _WebSocket()
    websocket.state.file_subscriptions = {
        "a": {
            "channel_name": "files:repo:GLOBAL",
            "requested_channel": "files:repo:docs",
            "location": "repo",
            "scope": None,
            "organization_id": None,
            "prefix": "docs",
        },
        "b": {
            "channel_name": "files:repo:GLOBAL",
            "requested_channel": "files:repo:docs-again",
            "location": "repo",
            "scope": None,
            "organization_id": None,
            "prefix": "docs",
        },
    }

    await ws_mod._handle_file_message(
        websocket,
        user,
        "files:other:GLOBAL",
        {"type": "file_change", "path": "docs/a.md"},
    )
    websocket.send_json.assert_not_awaited()

    with patch.object(ws_mod, "_file_has_applicable_policy", AsyncMock(return_value=False)):
        await ws_mod._handle_file_message(
            websocket,
            user,
            "files:repo:GLOBAL",
            {"type": "file_policy_changed"},
        )

    assert websocket.send_json.await_count == 2
    assert websocket.state.file_subscriptions == {}

    websocket = _WebSocket()
    websocket.state.file_subscriptions = {
        "a": {
            "channel_name": "files:repo:GLOBAL",
            "requested_channel": "files:repo:docs",
            "location": "repo",
            "scope": None,
            "organization_id": None,
            "prefix": "docs",
        },
        "b": {
            "channel_name": "files:repo:GLOBAL",
            "requested_channel": "files:repo:docs-again",
            "location": "repo",
            "scope": None,
            "organization_id": None,
            "prefix": "docs",
        },
    }

    with patch.object(ws_mod, "_file_allowed", AsyncMock(return_value=True)) as allowed:
        await ws_mod._handle_file_message(
            websocket,
            user,
            "files:repo:GLOBAL",
            {
                "type": "file_change",
                "location": "repo",
                "scope": "GLOBAL",
                "path": "/docs/a.md",
                "action": "write",
            },
        )

    allowed.assert_awaited_once()
    websocket.send_json.assert_awaited_once_with(
        {
            "type": "file_change",
            "channel": "files:repo:docs",
            "location": "repo",
            "scope": "GLOBAL",
            "path": "docs/a.md",
            "action": "write",
        }
    )


@pytest.mark.asyncio
async def test_can_access_conversation_rejects_bad_ids_without_db_lookup():
    assert await ws_mod.can_access_conversation(_user(), "not-a-uuid") == (False, None)


@pytest.mark.asyncio
async def test_can_access_conversation_returns_loaded_owned_conversation():
    conversation = SimpleNamespace(id=uuid4())
    with patch.object(
        ws_mod,
        "get_db_context",
        _db_context(_Db(_ScalarResult(conversation))),
    ):
        assert await ws_mod.can_access_conversation(_user(), str(conversation.id)) == (
            True,
            conversation,
        )


@pytest.mark.asyncio
async def test_can_access_execution_allows_superusers_and_hides_missing_rows():
    assert await ws_mod.can_access_execution(_user(is_superuser=True), "not-a-uuid")

    with patch.object(ws_mod, "get_db_context", _db_context(_Db(_ScalarResult(None)))):
        assert await ws_mod.can_access_execution(_user(), str(uuid4()))


@pytest.mark.asyncio
async def test_can_access_execution_checks_owner_for_existing_rows():
    user = _user()

    with patch.object(
        ws_mod,
        "get_db_context",
        _db_context(_Db(_ScalarResult(user.user_id))),
    ):
        assert await ws_mod.can_access_execution(user, str(uuid4()))

    with patch.object(
        ws_mod,
        "get_db_context",
        _db_context(_Db(_ScalarResult(uuid4()))),
    ):
        assert not await ws_mod.can_access_execution(user, str(uuid4()))


@pytest.mark.asyncio
async def test_can_access_execution_checks_embed_session_redis_key():
    user = _user(embed=True, jti="embed-session")
    redis = SimpleNamespace(exists=AsyncMock(return_value=1))

    @asynccontextmanager
    async def redis_ctx():
        yield redis

    with patch("src.core.cache.redis_client.get_redis", return_value=redis_ctx()):
        assert await ws_mod.can_access_execution(user, "not-a-uuid")

    redis.exists.assert_awaited_once()


@pytest.mark.asyncio
async def test_can_access_agent_run_rejects_bad_id_and_delegates_loader():
    user = _user()

    assert not await ws_mod.can_access_agent_run(user, "not-a-uuid")

    with (
        patch.object(ws_mod, "get_db_context", _db_context(object())),
        patch.object(ws_mod, "load_agent_run_for_user", AsyncMock(return_value=object())) as loader,
    ):
        assert await ws_mod.can_access_agent_run(user, str(uuid4()))

    loader.assert_awaited_once()


@pytest.mark.asyncio
async def test_can_access_app_handles_bad_missing_global_and_org_rows():
    user = _user()

    assert not await ws_mod.can_access_app(user, "not-a-uuid")
    assert await ws_mod.can_access_app(_user(is_superuser=True), "not-a-uuid")

    with patch.object(ws_mod, "get_db_context", _db_context(_Db(_OneResult(None)))):
        assert await ws_mod.can_access_app(user, str(uuid4()))

    with patch.object(ws_mod, "get_db_context", _db_context(_Db(_OneResult((None,))))):
        assert await ws_mod.can_access_app(user, str(uuid4()))

    with patch.object(
        ws_mod,
        "get_db_context",
        _db_context(_Db(_OneResult((user.organization_id,)))),
    ):
        assert await ws_mod.can_access_app(user, str(uuid4()))

    with patch.object(
        ws_mod,
        "get_db_context",
        _db_context(_Db(_OneResult((uuid4(),)))),
    ):
        assert not await ws_mod.can_access_app(user, str(uuid4()))


@pytest.mark.asyncio
async def test_generate_conversation_title_strips_quotes_and_truncates():
    long_title = f'"{"A" * 130}"'
    llm_client = SimpleNamespace(
        complete=AsyncMock(return_value=SimpleNamespace(content=long_title))
    )

    with patch(
        "src.services.llm.get_llm_client",
        new=AsyncMock(return_value=llm_client),
    ):
        result = await ws_mod._generate_conversation_title(
            db=object(),
            conversation=SimpleNamespace(id=uuid4()),
            user_message="Please summarize this support issue.",
        )

    assert result == ("A" * 97) + "..."
    llm_client.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_conversation_title_returns_none_on_empty_or_failure():
    empty_client = SimpleNamespace(
        complete=AsyncMock(return_value=SimpleNamespace(content=""))
    )

    with patch(
        "src.services.llm.get_llm_client",
        new=AsyncMock(return_value=empty_client),
    ):
        assert await ws_mod._generate_conversation_title(
            db=object(),
            conversation=SimpleNamespace(id=uuid4()),
            user_message="hello",
        ) is None

    with patch(
        "src.services.llm.get_llm_client",
        new=AsyncMock(side_effect=RuntimeError("llm unavailable")),
    ):
        assert await ws_mod._generate_conversation_title(
            db=object(),
            conversation=SimpleNamespace(id=uuid4()),
            user_message="hello",
        ) is None
