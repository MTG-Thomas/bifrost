from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from src.core import auth
from src.core.principal import UserPrincipal


def _request(*, cookies=None, headers=None, query_params=None):
    return SimpleNamespace(
        cookies=cookies or {},
        headers=headers or {},
        query_params=query_params or {},
    )


def _credentials(token: str):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _payload(**overrides):
    data = {
        "sub": str(uuid4()),
        "email": "user@example.test",
        "name": "User",
        "org_id": str(uuid4()),
        "is_superuser": False,
    }
    data.update(overrides)
    return data


def _principal(**overrides):
    user = UserPrincipal(
        user_id=uuid4(),
        email="user@example.test",
        organization_id=uuid4(),
        name="User",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        roles=[],
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


@pytest.mark.asyncio
async def test_optional_user_prefers_authorization_header_over_cookie(monkeypatch):
    calls = []

    def decode_token(token, *, expected_type):
        calls.append((token, expected_type))
        return _payload(email=f"{token}@example.test")

    monkeypatch.setattr(auth, "decode_token", decode_token)

    user = await auth.get_current_user_optional(
        _request(cookies={"access_token": "cookie-token"}),
        _credentials("header-token"),
        object(),
    )

    assert user is not None
    assert user.email == "header-token@example.test"
    assert calls == [("header-token", "access")]


@pytest.mark.asyncio
async def test_optional_user_accepts_embed_cookie_as_typed_access_token(monkeypatch):
    calls = []

    def decode_token(token, *, expected_type):
        calls.append((token, expected_type))
        return _payload(org_id=None, embed=True, app_id="app-1", form_id="form-1")

    monkeypatch.setattr(auth, "decode_token", decode_token)

    user = await auth.get_current_user_optional(
        _request(cookies={"embed_token": "embed-token"}),
        None,
        object(),
    )

    assert user is not None
    assert user.embed is True
    assert user.organization_id is None
    assert user.app_id == "app-1"
    assert user.form_id == "form-1"
    assert calls == [("embed-token", "access")]


@pytest.mark.asyncio
async def test_optional_user_rejects_mcp_scoped_rest_token(monkeypatch):
    monkeypatch.setattr(auth, "decode_token", lambda token, *, expected_type: _payload(mcp=True))

    assert (
        await auth.get_current_user_optional(
            _request(),
            _credentials("mcp-token"),
            object(),
        )
        is None
    )


@pytest.mark.asyncio
async def test_required_user_and_active_superuser_guards_raise_http_errors(monkeypatch):
    async def no_current_user(*args):
        return None

    monkeypatch.setattr(auth, "get_current_user_optional", no_current_user)

    with pytest.raises(HTTPException) as unauthenticated:
        await auth.get_current_user(_request(), None, object())
    assert unauthenticated.value.status_code == 401

    inactive = _principal(is_active=False)
    with pytest.raises(HTTPException) as forbidden_inactive:
        await auth.get_current_active_user(inactive)
    assert forbidden_inactive.value.status_code == 403

    regular = _principal(is_superuser=False)
    with pytest.raises(HTTPException) as forbidden_superuser:
        await auth.get_current_superuser(regular)
    assert forbidden_superuser.value.status_code == 403


@pytest.mark.asyncio
async def test_active_and_superuser_guards_return_valid_user() -> None:
    admin = _principal(is_superuser=True)

    assert await auth.get_current_active_user(admin) is admin
    assert await auth.get_current_superuser(admin) is admin


def test_execution_context_properties() -> None:
    user = _principal(is_superuser=True)
    ctx = auth.ExecutionContext(user=user, org_id=user.organization_id, db=object())

    assert ctx.scope == str(user.organization_id)
    assert ctx.user_id == str(user.user_id)
    assert ctx.is_global_scope is False
    assert ctx.is_platform_admin is True

    global_ctx = auth.ExecutionContext(user=user, org_id=None, db=object())
    assert global_ctx.scope == "GLOBAL"
    assert global_ctx.is_global_scope is True


@pytest.mark.asyncio
async def test_execution_context_rejects_solution_app_mismatch(monkeypatch):
    solution_id = uuid4()
    app_solution_id = uuid4()
    app_id = uuid4()

    class Db:
        async def get(self, model, item_id):
            if item_id == solution_id:
                return SimpleNamespace(id=solution_id, status="active")
            if item_id == app_id:
                return SimpleNamespace(id=app_id, solution_id=app_solution_id)
            if item_id == app_solution_id:
                return SimpleNamespace(id=app_solution_id, status="active")
            return None

    async def no_roles(user_id, db):
        return [], []

    monkeypatch.setattr(auth, "get_user_roles", no_roles)
    request = _request(
        headers={"X-Bifrost-App": str(app_id)},
        query_params={"solution": str(solution_id)},
    )

    with pytest.raises(HTTPException) as exc:
        await auth.get_execution_context(request, _principal(), Db())

    assert exc.value.status_code == 400
    assert "does not belong" in exc.value.detail


@pytest.mark.asyncio
async def test_execution_context_rejects_inactive_solution(monkeypatch):
    solution_id = uuid4()

    class Db:
        async def get(self, model, item_id):
            return SimpleNamespace(id=item_id, status="uninstalled")

    async def no_roles(user_id, db):
        return [], []

    monkeypatch.setattr(auth, "get_user_roles", no_roles)

    with pytest.raises(HTTPException) as exc:
        await auth.get_execution_context(
            _request(query_params={"solution": str(solution_id)}),
            _principal(),
            Db(),
        )

    assert exc.value.status_code == 409
    assert "inactive" in exc.value.detail


@pytest.mark.asyncio
async def test_websocket_auth_uses_header_and_rejects_mcp_token(monkeypatch):
    calls = []

    def decode_token(token, *, expected_type):
        calls.append((token, expected_type))
        return _payload(mcp=True)

    monkeypatch.setattr(auth, "decode_token", decode_token)
    websocket = _request(headers={"authorization": "Bearer ws-token"})

    assert await auth.get_current_user_ws(websocket) is None
    assert calls == [("ws-token", "access")]


@pytest.mark.asyncio
async def test_websocket_auth_accepts_query_token_for_embed_browser_clients(monkeypatch):
    monkeypatch.setattr(
        auth,
        "decode_token",
        lambda token, *, expected_type: _payload(org_id=None, embed=True),
    )

    user = await auth.get_current_user_ws(_request(query_params={"token": "query-token"}))

    assert user is not None
    assert user.embed is True
    assert user.organization_id is None


@pytest.mark.asyncio
async def test_websocket_auth_accepts_valid_cookie_token(monkeypatch):
    org_id = uuid4()
    monkeypatch.setattr(
        auth,
        "decode_token",
        lambda token, *, expected_type: _payload(org_id=str(org_id)),
    )

    user = await auth.get_current_user_ws(
        _request(cookies={"access_token": "cookie-token"})
    )

    assert user is not None
    assert user.organization_id == org_id
    assert user.email == "user@example.test"


@pytest.mark.asyncio
async def test_websocket_auth_rejects_bad_subject_missing_email_and_bad_org(monkeypatch):
    websocket = _request(cookies={"access_token": "token"})

    monkeypatch.setattr(auth, "decode_token", lambda token, *, expected_type: _payload(sub="bad"))
    assert await auth.get_current_user_ws(websocket) is None

    monkeypatch.setattr(
        auth,
        "decode_token",
        lambda token, *, expected_type: {
            key: value for key, value in _payload().items() if key != "email"
        },
    )
    assert await auth.get_current_user_ws(websocket) is None

    monkeypatch.setattr(
        auth,
        "decode_token",
        lambda token, *, expected_type: _payload(org_id="not-a-uuid"),
    )
    assert await auth.get_current_user_ws(websocket) is None


@pytest.mark.asyncio
async def test_get_current_user_from_db_returns_user_or_raises(monkeypatch):
    found = object()

    class UserRepository:
        def __init__(self, db):
            self.db = db

        async def get_by_id(self, user_id):
            return found

    monkeypatch.setattr("src.repositories.users.UserRepository", UserRepository)
    assert await auth.get_current_user_from_db(_principal(), object()) is found

    class MissingUserRepository:
        def __init__(self, db):
            self.db = db

        async def get_by_id(self, user_id):
            return None

    monkeypatch.setattr("src.repositories.users.UserRepository", MissingUserRepository)
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user_from_db(_principal(), object())

    assert exc.value.status_code == 404
