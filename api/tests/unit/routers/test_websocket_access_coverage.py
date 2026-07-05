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


def _db_context(db):
    @asynccontextmanager
    async def fake_get_db_context():
        yield db

    return fake_get_db_context


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
