from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException, Response, status

from src.models.contracts.passkeys import (
    PasskeyAuthOptionsRequest,
    PasskeyAuthVerifyRequest,
    PasskeyRegistrationVerifyRequest,
)
from src.routers import oauth_sso, passkeys
from src.services.oauth_sso import OAuthError, OAuthTokens, OAuthUserInfo


def _db() -> AsyncMock:
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


def _user(**overrides: object) -> SimpleNamespace:
    data = {
        "id": UUID("11111111-1111-1111-1111-111111111111"),
        "user_id": UUID("11111111-1111-1111-1111-111111111111"),
        "email": "user@example.com",
        "name": "User",
        "is_superuser": False,
        "organization_id": UUID("22222222-2222-2222-2222-222222222222"),
        "hashed_password": "hash",
        "is_active": True,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_get_oauth_providers_uses_known_and_fallback_display_info() -> None:
    service = MagicMock()
    service.get_available_providers = AsyncMock(return_value=["microsoft", "custom"])

    with patch.object(oauth_sso, "OAuthService", return_value=service):
        result = await oauth_sso.get_oauth_providers(_db())

    assert [(p.name, p.display_name, p.icon) for p in result.providers] == [
        ("microsoft", "Microsoft", "microsoft"),
        ("custom", "Custom", None),
    ]


@pytest.mark.asyncio
async def test_init_oauth_stores_server_side_pkce_state() -> None:
    redis = AsyncMock()
    service = MagicMock()
    service.get_authorization_url = AsyncMock(return_value="https://idp.test/auth")

    with (
        patch.object(oauth_sso, "OAuthService", return_value=service),
        patch.object(oauth_sso.OAuthService, "generate_code_verifier", return_value="verifier"),
        patch.object(oauth_sso.OAuthService, "generate_state", return_value="state-1"),
        patch.object(oauth_sso, "get_shared_redis", AsyncMock(return_value=redis)),
    ):
        result = await oauth_sso.init_oauth(
            "microsoft",
            _db(),
            redirect_uri="https://app.test/callback",
        )

    assert result.authorization_url == "https://idp.test/auth"
    redis.setex.assert_awaited_once()
    assert redis.setex.await_args.args[0].endswith("state-1")
    assert "verifier" in redis.setex.await_args.args[2]


@pytest.mark.asyncio
async def test_init_oauth_maps_oauth_error_to_bad_request() -> None:
    service = MagicMock()
    service.get_authorization_url = AsyncMock(side_effect=OAuthError("provider disabled"))

    with (
        patch.object(oauth_sso, "OAuthService", return_value=service),
        patch.object(oauth_sso.OAuthService, "generate_code_verifier", return_value="verifier"),
        patch.object(oauth_sso.OAuthService, "generate_state", return_value="state-1"),
    ):
        with pytest.raises(HTTPException) as exc:
            await oauth_sso.init_oauth(
                "microsoft",
                _db(),
                redirect_uri="https://app.test/callback",
            )

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc.value.detail == "provider disabled"


@pytest.mark.asyncio
async def test_oauth_callback_rejects_missing_state() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)

    with patch.object(oauth_sso, "get_shared_redis", AsyncMock(return_value=redis)):
        with pytest.raises(HTTPException) as exc:
            await oauth_sso.oauth_callback(
                oauth_sso.OAuthCallbackRequest(
                    provider="microsoft",
                    code="code",
                    state="expired",
                ),
                request=MagicMock(),
                response=Response(),
                db=_db(),
            )

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc.value.detail == "Invalid or expired OAuth state"
    redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_oauth_callback_rejects_bad_state_payload_after_single_use_delete() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"{not-json")
    redis.delete = AsyncMock()

    with patch.object(oauth_sso, "get_shared_redis", AsyncMock(return_value=redis)):
        with pytest.raises(HTTPException) as exc:
            await oauth_sso.oauth_callback(
                oauth_sso.OAuthCallbackRequest(
                    provider="microsoft",
                    code="code",
                    state="state-1",
                ),
                request=MagicMock(),
                response=Response(),
                db=_db(),
            )

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc.value.detail == "Invalid OAuth state data"
    redis.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_oauth_callback_provisions_user_links_oauth_and_sets_cookies() -> None:
    db = _db()
    response = Response()
    redis = AsyncMock()
    redis.get = AsyncMock(
        return_value=b'{"code_verifier":"verifier-1","redirect_uri":"https://app.test/callback"}'
    )
    redis.delete = AsyncMock()
    redis.setex = AsyncMock()
    service = MagicMock()
    service.exchange_code_for_tokens = AsyncMock(
        return_value=OAuthTokens(access_token="provider-access", refresh_token="provider-refresh")
    )
    service.get_user_info = AsyncMock(
        return_value=OAuthUserInfo(
            provider="microsoft",
            provider_user_id="provider-user-1",
            email="new@example.com",
            name="New User",
            email_verified=True,
        )
    )
    service.find_user_by_oauth = AsyncMock(return_value=None)
    service.link_oauth_account = AsyncMock()
    provisioned_user = _user(
        email="new@example.com",
        name="New User",
        is_superuser=True,
    )

    with (
        patch.object(oauth_sso, "OAuthService", return_value=service),
        patch.object(oauth_sso, "get_shared_redis", AsyncMock(return_value=redis)),
        patch.object(
            oauth_sso,
            "ensure_user_provisioned",
            AsyncMock(return_value=SimpleNamespace(user=provisioned_user)),
        ) as ensure_user,
        patch.object(oauth_sso, "get_user_roles", AsyncMock(return_value=["admin"])),
        patch.object(oauth_sso, "resolve_external_claim", AsyncMock(return_value=False)),
        patch.object(oauth_sso, "create_access_token", return_value="access.jwt") as access,
        patch.object(oauth_sso, "create_refresh_token", return_value=("refresh.jwt", "jti-1")) as refresh,
        patch.object(oauth_sso, "generate_csrf_token", return_value="csrf-1"),
    ):
        result = await oauth_sso.oauth_callback(
            oauth_sso.OAuthCallbackRequest(
                provider="microsoft",
                code="code-1",
                state="state-1",
            ),
            request=MagicMock(),
            response=response,
            db=db,
        )

    assert result.access_token == "access.jwt"
    assert result.refresh_token == "refresh.jwt"
    redis.delete.assert_awaited_once()
    redis.setex.assert_awaited_once()
    ensure_user.assert_awaited_once_with(
        db=db,
        email="new@example.com",
        name="New User",
    )
    service.link_oauth_account.assert_awaited_once()
    db.commit.assert_awaited_once()
    token_claims = access.call_args.kwargs["data"]
    assert token_claims["email"] == "new@example.com"
    assert token_claims["roles"] == ["authenticated", "admin"]
    refresh.assert_called_once_with(data={"sub": str(provisioned_user.id)})
    assert "access_token" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_get_linked_accounts_maps_provider_accounts() -> None:
    linked_at = datetime(2026, 7, 5, tzinfo=timezone.utc)
    service = MagicMock()
    service.get_user_oauth_accounts = AsyncMock(
        return_value=[
            SimpleNamespace(
                provider_id="microsoft",
                email="user@example.com",
                created_at=linked_at,
                last_login=None,
            )
        ]
    )

    with patch.object(oauth_sso, "OAuthService", return_value=service):
        result = await oauth_sso.get_linked_accounts(_user(), _db())

    assert len(result.accounts) == 1
    assert result.accounts[0].provider == "microsoft"
    assert result.accounts[0].provider_email == "user@example.com"
    assert result.accounts[0].linked_at == linked_at.isoformat()
    assert result.accounts[0].last_used_at is None


@pytest.mark.asyncio
async def test_unlink_oauth_account_prevents_last_login_method_removal() -> None:
    service = MagicMock()
    service.get_user_oauth_accounts = AsyncMock(
        return_value=[SimpleNamespace(provider_id="microsoft")]
    )

    with (
        patch.object(oauth_sso, "get_current_user_from_db", AsyncMock(return_value=_user(hashed_password=None))),
        patch.object(oauth_sso, "OAuthService", return_value=service),
    ):
        with pytest.raises(HTTPException) as exc:
            await oauth_sso.unlink_oauth_account("microsoft", _user(), _db())

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    service.unlink_oauth_account.assert_not_called()


@pytest.mark.asyncio
async def test_unlink_oauth_account_commits_success_when_password_exists() -> None:
    db = _db()
    service = MagicMock()
    service.get_user_oauth_accounts = AsyncMock(
        return_value=[SimpleNamespace(provider_id="microsoft")]
    )
    service.unlink_oauth_account = AsyncMock(return_value=True)

    with (
        patch.object(oauth_sso, "get_current_user_from_db", AsyncMock(return_value=_user(hashed_password="hash"))),
        patch.object(oauth_sso, "OAuthService", return_value=service),
    ):
        result = await oauth_sso.unlink_oauth_account("microsoft", _user(), db)

    assert result == {"message": "Microsoft account unlinked"}
    service.unlink_oauth_account.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_unlink_oauth_account_returns_404_when_provider_not_linked() -> None:
    db = _db()
    service = MagicMock()
    service.get_user_oauth_accounts = AsyncMock(
        return_value=[
            SimpleNamespace(provider_id="microsoft"),
            SimpleNamespace(provider_id="google"),
        ]
    )
    service.unlink_oauth_account = AsyncMock(return_value=False)

    with (
        patch.object(oauth_sso, "get_current_user_from_db", AsyncMock(return_value=_user(hashed_password=None))),
        patch.object(oauth_sso, "OAuthService", return_value=service),
    ):
        with pytest.raises(HTTPException) as exc:
            await oauth_sso.unlink_oauth_account("microsoft", _user(), db)

    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc.value.detail == "No microsoft account linked"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_registration_commits_and_returns_passkey_metadata() -> None:
    db = _db()
    service = MagicMock()
    service.verify_registration = AsyncMock(
        return_value=SimpleNamespace(id=UUID("33333333-3333-3333-3333-333333333333"), name="Laptop")
    )

    with patch.object(passkeys, "PasskeyService", return_value=service):
        result = await passkeys.verify_registration(
            PasskeyRegistrationVerifyRequest(
                credential={"id": "credential-1"},
                device_name="Laptop",
            ),
            _user(),
            db,
        )

    assert result.verified is True
    assert result.name == "Laptop"
    service.verify_registration.assert_awaited_once()
    assert '"credential-1"' in service.verify_registration.await_args.kwargs["credential_json"]
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_authentication_options_returns_challenge_and_options() -> None:
    service = MagicMock()
    service.generate_authentication_options = AsyncMock(
        return_value=("challenge-1", {"publicKey": {"challenge": "abc"}})
    )

    with patch.object(passkeys, "PasskeyService", return_value=service):
        result = await passkeys.get_authentication_options(
            PasskeyAuthOptionsRequest(email="user@example.com"),
            _db(),
        )

    assert result.challenge_id == "challenge-1"
    assert result.options["publicKey"]["challenge"] == "abc"


@pytest.mark.asyncio
async def test_verify_authentication_maps_value_error_to_unauthorized() -> None:
    service = MagicMock()
    service.verify_authentication = AsyncMock(side_effect=ValueError("bad challenge"))

    with patch.object(passkeys, "PasskeyService", return_value=service):
        with pytest.raises(HTTPException) as exc:
            await passkeys.verify_authentication(
                PasskeyAuthVerifyRequest(
                    challenge_id="challenge-1",
                    credential={"id": "credential-1"},
                ),
                Response(),
                _db(),
            )

    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.asyncio
async def test_list_passkeys_maps_public_fields() -> None:
    created = datetime(2026, 7, 5, tzinfo=timezone.utc)
    service = MagicMock()
    service.list_passkeys = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=UUID("44444444-4444-4444-4444-444444444444"),
                name="Phone",
                device_type="single_device",
                backed_up=False,
                created_at=created,
                last_used_at=None,
            )
        ]
    )

    with patch.object(passkeys, "PasskeyService", return_value=service):
        result = await passkeys.list_passkeys(_user(), _db())

    assert result.count == 1
    assert result.passkeys[0].name == "Phone"


@pytest.mark.asyncio
async def test_delete_passkey_returns_not_found_without_commit() -> None:
    db = _db()
    service = MagicMock()
    service.delete_passkey = AsyncMock(return_value=False)

    with patch.object(passkeys, "PasskeyService", return_value=service):
        with pytest.raises(HTTPException) as exc:
            await passkeys.delete_passkey(
                UUID("55555555-5555-5555-5555-555555555555"),
                _user(),
                db,
            )

    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    db.commit.assert_not_awaited()
