from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.models import User
from src.services.oauth_sso import OAuthError, OAuthService, OAuthTokens


@pytest.fixture
def db_session() -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def service(db_session: AsyncMock) -> OAuthService:
    return OAuthService(db_session)


def _provider_config(**overrides: object) -> SimpleNamespace:
    values = {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "tenant_id": None,
        "discovery_url": None,
        "display_name": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _AsyncClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self) -> "_AsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, **kwargs: object) -> SimpleNamespace:
        raise AssertionError(f"unexpected GET {url} {kwargs}")

    async def post(self, url: str, **kwargs: object) -> SimpleNamespace:
        raise AssertionError(f"unexpected POST {url} {kwargs}")


def test_pkce_and_state_helpers(service: OAuthService) -> None:
    verifier = service.generate_code_verifier()
    challenge = service.generate_code_challenge("known-verifier")
    state = service.generate_state()

    assert 43 <= len(verifier) <= 128
    assert challenge == "GZgROX6_AnvkowfutuOh_RiBDjJoEWf1Zz8BUNStfzM"
    assert "=" not in challenge
    assert len(state) >= 32


async def test_fetch_oidc_discovery_caches_valid_document(
    service: OAuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    class Client(_AsyncClient):
        async def get(self, url: str, **kwargs: object) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            assert url == "https://issuer/.well-known/openid-configuration"
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "issuer": "https://issuer",
                    "authorization_endpoint": "https://issuer/auth",
                    "token_endpoint": "https://issuer/token",
                    "userinfo_endpoint": "https://issuer/me",
                },
            )

    monkeypatch.setattr("src.services.oauth_sso.httpx.AsyncClient", Client)

    first = await service._fetch_oidc_discovery(
        "https://issuer/.well-known/openid-configuration"
    )
    second = await service._fetch_oidc_discovery(
        "https://issuer/.well-known/openid-configuration"
    )

    assert first is second
    assert calls == 1
    assert first["authorization_endpoint"] == "https://issuer/auth"


async def test_fetch_oidc_discovery_rejects_http_error(
    service: OAuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Client(_AsyncClient):
        async def get(self, url: str, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(status_code=503, json=lambda: {})

    monkeypatch.setattr("src.services.oauth_sso.httpx.AsyncClient", Client)

    with pytest.raises(OAuthError, match="HTTP 503"):
        await service._fetch_oidc_discovery("https://issuer/config")


async def test_fetch_oidc_discovery_rejects_missing_required_field(
    service: OAuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Client(_AsyncClient):
        async def get(self, url: str, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"authorization_endpoint": "https://issuer/auth"},
            )

    monkeypatch.setattr("src.services.oauth_sso.httpx.AsyncClient", Client)

    with pytest.raises(OAuthError, match="missing required fields"):
        await service._fetch_oidc_discovery("https://issuer/config")


async def test_get_provider_config_microsoft_uses_tenant(service: OAuthService) -> None:
    service._config_service.get_provider_config = AsyncMock(
        return_value=_provider_config(tenant_id="tenant-1")
    )

    config = await service.get_provider_config("microsoft")

    assert config["client_id"] == "client-id"
    assert "tenant-1/oauth2/v2.0/authorize" in config["authorize_url"]
    assert "User.Read" in config["scopes"]


async def test_get_provider_config_google(service: OAuthService) -> None:
    service._config_service.get_provider_config = AsyncMock(return_value=_provider_config())

    config = await service.get_provider_config("google")

    assert config["token_url"] == "https://oauth2.googleapis.com/token"
    assert config["scopes"] == ["openid", "email", "profile"]


async def test_get_provider_config_oidc_uses_discovery(service: OAuthService) -> None:
    service._config_service.get_provider_config = AsyncMock(
        return_value=_provider_config(
            discovery_url="https://issuer/config",
            display_name="Corp SSO",
        )
    )
    service._fetch_oidc_discovery = AsyncMock(
        return_value={
            "authorization_endpoint": "https://issuer/auth",
            "token_endpoint": "https://issuer/token",
            "userinfo_endpoint": "https://issuer/me",
        }
    )

    config = await service.get_provider_config("oidc")

    assert config["display_name"] == "Corp SSO"
    assert config["authorize_url"] == "https://issuer/auth"
    assert config["userinfo_url"] == "https://issuer/me"


@pytest.mark.parametrize(
    ("provider", "message"),
    [
        ("microsoft", "Microsoft OAuth is not configured"),
        ("google", "Google OAuth is not configured"),
        ("oidc", "OIDC provider is not configured"),
        ("unknown", "Unknown OAuth provider"),
    ],
)
async def test_get_provider_config_errors(
    service: OAuthService, provider: str, message: str
) -> None:
    service._config_service.get_provider_config = AsyncMock(return_value=None)

    with pytest.raises(OAuthError, match=message):
        await service.get_provider_config(provider)


async def test_get_available_providers_delegates(service: OAuthService) -> None:
    service._config_service.get_available_providers = AsyncMock(
        return_value=["microsoft", "google"]
    )

    assert await service.get_available_providers() == ["microsoft", "google"]


async def test_get_authorization_url_adds_provider_specific_params(
    service: OAuthService,
) -> None:
    service.get_provider_config = AsyncMock(
        return_value={
            "client_id": "google-client",
            "authorize_url": "https://accounts.example/auth",
            "scopes": ["openid", "email"],
        }
    )

    url = await service.get_authorization_url(
        "google",
        redirect_uri="https://app/callback",
        state="state-1",
        code_verifier="verifier-1",
    )

    assert url.startswith("https://accounts.example/auth?")
    assert "client_id=google-client" in url
    assert "response_type=code" in url
    assert "scope=openid+email" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "code_challenge_method=S256" in url


async def test_get_authorization_url_adds_microsoft_response_mode(
    service: OAuthService,
) -> None:
    service.get_provider_config = AsyncMock(
        return_value={
            "client_id": "ms-client",
            "authorize_url": "https://login.example/auth",
            "scopes": ["openid"],
        }
    )

    url = await service.get_authorization_url(
        "microsoft",
        redirect_uri="https://app/callback",
        state="state-1",
        code_verifier="verifier-1",
    )

    assert "response_mode=query" in url


async def test_exchange_code_for_tokens_success(
    service: OAuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: dict[str, object] = {}
    service.get_provider_config = AsyncMock(
        return_value={
            "client_id": "client-id",
            "client_secret": "client-secret",
            "token_url": "https://issuer/token",
        }
    )

    class Client(_AsyncClient):
        async def post(self, url: str, **kwargs: object) -> SimpleNamespace:
            posted["url"] = url
            posted.update(kwargs)
            return SimpleNamespace(
                status_code=200,
                headers={"content-type": "application/json"},
                json=lambda: {
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "id_token": "id-token",
                    "scope": "openid email",
                },
            )

    monkeypatch.setattr("src.services.oauth_sso.httpx.AsyncClient", Client)

    tokens = await service.exchange_code_for_tokens(
        "oidc", "code-1", "https://app/callback", "verifier-1"
    )

    assert tokens == OAuthTokens(
        access_token="access",
        refresh_token="refresh",
        expires_in=3600,
        token_type="Bearer",
        id_token="id-token",
        scope="openid email",
    )
    assert posted["url"] == "https://issuer/token"
    assert posted["data"]["client_secret"] == "client-secret"  # type: ignore[index]


async def test_exchange_code_for_tokens_raises_provider_error(
    service: OAuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.get_provider_config = AsyncMock(
        return_value={"client_id": "client-id", "token_url": "https://issuer/token"}
    )

    class Client(_AsyncClient):
        async def post(self, url: str, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                status_code=400,
                headers={"content-type": "application/json"},
                json=lambda: {"error_description": "bad verifier"},
                text="bad verifier",
            )

    monkeypatch.setattr("src.services.oauth_sso.httpx.AsyncClient", Client)

    with pytest.raises(OAuthError, match="bad verifier"):
        await service.exchange_code_for_tokens(
            "oidc", "code-1", "https://app/callback", "verifier-1"
        )


async def test_get_user_info_fetches_and_parses(
    service: OAuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.get_provider_config = AsyncMock(
        return_value={"userinfo_url": "https://issuer/me"}
    )

    class Client(_AsyncClient):
        async def get(self, url: str, **kwargs: object) -> SimpleNamespace:
            assert kwargs["headers"] == {"Authorization": "Bearer access-token"}
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "sub": "sub-1",
                    "email": "person@example.com",
                    "name": "Person",
                    "picture": "https://example.com/me.jpg",
                    "email_verified": True,
                },
            )

    monkeypatch.setattr("src.services.oauth_sso.httpx.AsyncClient", Client)

    info = await service.get_user_info("google", OAuthTokens(access_token="access-token"))

    assert info.provider == "google"
    assert info.provider_user_id == "sub-1"
    assert info.email_verified is True


async def test_get_user_info_raises_on_http_error(
    service: OAuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.get_provider_config = AsyncMock(
        return_value={"userinfo_url": "https://issuer/me"}
    )

    class Client(_AsyncClient):
        async def get(self, url: str, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(status_code=401, text="unauthorized")

    monkeypatch.setattr("src.services.oauth_sso.httpx.AsyncClient", Client)

    with pytest.raises(OAuthError, match="unauthorized"):
        await service.get_user_info("google", OAuthTokens(access_token="bad"))


def test_parse_user_info_microsoft(service: OAuthService) -> None:
    info = service._parse_user_info(
        "microsoft",
        {
            "id": "ms-id",
            "mail": None,
            "userPrincipalName": "ada@example.com",
            "displayName": "Ada Lovelace",
        },
    )

    assert info.provider_user_id == "ms-id"
    assert info.email == "ada@example.com"
    assert info.name == "Ada Lovelace"
    assert info.email_verified is True


async def test_get_oauth_account_returns_scalar(service: OAuthService) -> None:
    account = object()
    result = MagicMock()
    result.scalar_one_or_none.return_value = account
    service.db.execute.return_value = result

    assert await service.get_oauth_account("google", "sub-1") is account


async def test_get_user_oauth_accounts_returns_list(service: OAuthService) -> None:
    accounts = [object(), object()]
    result = MagicMock()
    result.scalars.return_value.all.return_value = accounts
    service.db.execute.return_value = result

    assert await service.get_user_oauth_accounts(uuid4()) == accounts


async def test_link_oauth_account_updates_existing(service: OAuthService) -> None:
    existing = SimpleNamespace(last_login=None)
    service.get_oauth_account = AsyncMock(return_value=existing)
    user = cast(User, SimpleNamespace(id=uuid4(), email="ada@example.com"))
    user_info = service._parse_user_info(
        "google", {"sub": "sub-1", "email": "ada@example.com"}
    )

    assert await service.link_oauth_account(user, user_info) is existing
    assert existing.last_login is not None
    service.db.add.assert_not_called()
    service.db.flush.assert_awaited_once()


async def test_link_oauth_account_creates_new(service: OAuthService) -> None:
    service.get_oauth_account = AsyncMock(return_value=None)
    user = cast(User, SimpleNamespace(id=uuid4(), email="ada@example.com"))
    user_info = service._parse_user_info(
        "google", {"sub": "sub-1", "email": "ada@example.com"}
    )

    account = await service.link_oauth_account(user, user_info)

    assert account.user_id == user.id
    assert account.provider_id == "google"
    assert account.provider_user_id == "sub-1"
    service.db.add.assert_called_once_with(account)
    service.db.flush.assert_awaited_once()


async def test_unlink_oauth_account_returns_rowcount(service: OAuthService) -> None:
    result = SimpleNamespace(rowcount=1)
    service.db.execute.return_value = result

    assert await service.unlink_oauth_account(uuid4(), "google") is True
    service.db.flush.assert_awaited_once()


async def test_find_user_by_oauth_returns_scalar(service: OAuthService) -> None:
    user = object()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    service.db.execute.return_value = result

    assert await service.find_user_by_oauth("google", "sub-1") is user
