from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from src.core.principal import UserPrincipal
from src.routers import auth


class _Redis:
    def __init__(self, *, keys=None, delete_result=1):
        self.setex_calls = []
        self.deleted = []
        self._keys = list(keys or [])
        self._delete_result = delete_result

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))

    async def delete(self, *keys):
        self.deleted.extend(keys)
        return self._delete_result

    async def scan_iter(self, match):
        self.scan_match = match
        for key in self._keys:
            yield key


def _principal(**overrides):
    user = UserPrincipal(
        user_id=uuid4(),
        email="user@example.com",
        organization_id=uuid4(),
        name="User Name",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        roles=["authenticated", "operator"],
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


def _request_with_cookies(cookies):
    return SimpleNamespace(cookies=cookies)


def test_set_and_clear_auth_cookies():
    response = Response()

    with patch.object(auth, "generate_csrf_token", return_value="csrf-token"):
        auth.set_auth_cookies(response, "access-token", "refresh-token")

    cookie_headers = [
        value.decode("latin1")
        for key, value in response.raw_headers
        if key == b"set-cookie"
    ]
    assert any("access_token=access-token" in value and "HttpOnly" in value for value in cookie_headers)
    assert any("refresh_token=refresh-token" in value and "HttpOnly" in value for value in cookie_headers)
    assert any("csrf_token=csrf-token" in value and "SameSite=strict" in value for value in cookie_headers)

    clear_response = Response()
    auth.clear_auth_cookies(clear_response)
    clear_headers = [
        value.decode("latin1")
        for key, value in clear_response.raw_headers
        if key == b"set-cookie"
    ]
    assert sum("Max-Age=0" in value for value in clear_headers) == 3


@pytest.mark.asyncio
async def test_refresh_token_jti_helpers_use_expected_redis_keys():
    redis = _Redis(keys=[b"token-1", b"token-2"], delete_result=2)

    with patch.object(auth, "get_shared_redis", new=AsyncMock(return_value=redis)):
        await auth.store_refresh_token_jti("user-1", "jti-1")
        valid = await auth.validate_and_revoke_refresh_token_jti("user-1", "jti-1")
        revoked = await auth.revoke_all_user_refresh_tokens("user-1")

    assert redis.setex_calls[0][1] == auth.TTL_REFRESH_TOKEN
    assert "user-1" in redis.setex_calls[0][0]
    assert valid is True
    assert revoked == 2
    assert b"token-1" in redis.deleted and b"token-2" in redis.deleted


@pytest.mark.parametrize(
    ("redirect_uri", "detail"),
    [
        ("https://127.0.0.1:1234/callback", "must use http"),
        ("http://example.com:1234/callback", "must point to localhost"),
        ("http://localhost/callback", "must include a port"),
    ],
)
def test_validate_cli_redirect_uri_rejects_unsafe_targets(redirect_uri, detail):
    with pytest.raises(HTTPException) as exc:
        auth._validate_cli_redirect_uri(redirect_uri)

    assert exc.value.status_code == 400
    assert detail in exc.value.detail


def test_cli_redirect_with_params_preserves_existing_query_separator():
    response = auth._cli_redirect_with_params(
        "http://127.0.0.1:8787/callback?existing=1",
        "code=abc&state=xyz",
    )

    assert response.headers["location"] == (
        "http://127.0.0.1:8787/callback?existing=1&code=abc&state=xyz"
    )


def test_hash_and_device_user_code_helpers_format_values():
    code = auth._generate_user_code()

    assert auth._cli_native_auth_key("tx-1") == "bifrost:auth:cli-native:tx-1"
    assert len(auth._sha256_urlsafe("verifier")) == 43
    assert len(code) == 9
    assert code[4] == "-"
    assert not set(code) & {"O", "I", "S", "Z", "0", "1", "5", "2"}


@pytest.mark.asyncio
async def test_generate_login_tokens_commits_and_emits_audit():
    user = SimpleNamespace(
        id=uuid4(),
        email="person@example.com",
        name=None,
        is_superuser=True,
        organization_id=None,
        last_login=None,
    )
    db = AsyncMock()

    with (
        patch.object(auth, "get_user_roles", new=AsyncMock(return_value=["admin"])),
        patch.object(auth, "resolve_external_claim", new=AsyncMock(return_value=False)),
        patch.object(auth, "create_access_token", return_value="access"),
        patch.object(auth, "create_refresh_token", return_value=("refresh", "jti")),
        patch.object(auth, "store_refresh_token_jti", new=AsyncMock()) as store_jti,
        patch.object(auth, "emit_audit", new=AsyncMock()) as audit,
        patch.object(auth, "set_auth_cookies") as set_cookies,
    ):
        result = await auth._generate_login_tokens(user, db, response=Response())

    db.commit.assert_awaited_once()
    assert user.last_login is not None
    assert result.access_token == "access"
    assert result.refresh_token == "refresh"
    store_jti.assert_awaited_once_with(str(user.id), "jti")
    audit.assert_awaited_once()
    set_cookies.assert_called_once()


@pytest.mark.asyncio
async def test_logout_revokes_body_token_and_clears_cookies():
    user = _principal()
    response = Response()
    db = AsyncMock()

    with (
        patch.object(auth, "decode_token", return_value={"jti": "refresh-jti"}),
        patch.object(auth, "validate_and_revoke_refresh_token_jti", new=AsyncMock(return_value=True)) as revoke,
        patch.object(auth, "emit_audit", new=AsyncMock()) as audit,
    ):
        result = await auth.logout(
            _request_with_cookies({}),
            response,
            user,
            db,
            auth.LogoutRequest(refresh_token="body-token"),
        )

    assert result.message == "Logged out successfully"
    revoke.assert_awaited_once_with(str(user.user_id), "refresh-jti")
    audit.assert_awaited_once()
    assert any(key == b"set-cookie" for key, _ in response.raw_headers)


@pytest.mark.asyncio
async def test_revoke_all_sessions_returns_count_and_clears_cookies():
    user = _principal()
    response = Response()

    with patch.object(auth, "revoke_all_user_refresh_tokens", new=AsyncMock(return_value=4)) as revoke:
        result = await auth.revoke_all_sessions(response, user)

    revoke.assert_awaited_once_with(str(user.user_id))
    assert result.sessions_revoked == 4
    assert any(key == b"set-cookie" for key, _ in response.raw_headers)


@pytest.mark.asyncio
async def test_get_current_user_info_projects_principal_fields():
    user = _principal()

    result = await auth.get_current_user_info(user)

    assert result.id == str(user.user_id)
    assert result.email == user.email
    assert result.organization_id == str(user.organization_id)
    assert result.roles == ["authenticated", "operator"]

