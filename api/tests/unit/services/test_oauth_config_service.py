"""Unit tests for OAuth SSO configuration service."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.models.contracts.oauth_config import (
    GoogleOAuthConfigRequest,
    MicrosoftOAuthConfigRequest,
    OIDCConfigRequest,
    OAUTH_GOOGLE_CLIENT_ID,
    OAUTH_GOOGLE_CLIENT_SECRET,
    OAUTH_MICROSOFT_CLIENT_ID,
    OAUTH_MICROSOFT_CLIENT_SECRET,
    OAUTH_MICROSOFT_TENANT_ID,
    OAUTH_OIDC_CLIENT_ID,
    OAUTH_OIDC_CLIENT_SECRET,
    OAUTH_OIDC_DISCOVERY_URL,
    OAUTH_OIDC_DISPLAY_NAME,
)
from src.services import oauth_config_service
from src.services.oauth_config_service import OAuthConfigService, OAuthProviderConfig


class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeDb:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.added = []
        self.executed = []
        self.flushes = 0

    async def execute(self, statement):
        self.executed.append(statement)
        return _FakeResult(self._rows.pop(0) if self._rows else None)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushes += 1


class _FakeAsyncClient:
    response = None
    error = None
    calls = []

    def __init__(self, *, timeout):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url):
        self.__class__.calls.append((url, self.timeout))
        if self.__class__.error is not None:
            raise self.__class__.error
        return self.__class__.response


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_secret_decrypt_failure_log_omits_secret_material(caplog):
    """Decrypt failures should not log ciphertext, key names, or exception text."""
    service = OAuthConfigService(db=AsyncMock())
    service._get_config_value = AsyncMock(return_value="encrypted-secret-payload")  # type: ignore[method-assign]

    with (
        patch(
            "src.services.oauth_config_service.decrypt_secret",
            side_effect=RuntimeError("failed for super-secret-value"),
        ),
        caplog.at_level(logging.ERROR, logger="src.services.oauth_config_service"),
    ):
        result = await service._get_secret_value("oauth_client_secret")

    assert result is None
    log_text = caplog.text
    assert "RuntimeError" in log_text
    assert "oauth_client_secret" not in log_text
    assert "encrypted-secret-payload" not in log_text
    assert "super-secret-value" not in log_text


@pytest.mark.asyncio
async def test_get_secret_value_returns_none_when_config_missing() -> None:
    service = OAuthConfigService(db=AsyncMock())
    service._get_config_value = AsyncMock(return_value=None)  # type: ignore[method-assign]

    assert await service._get_secret_value("missing_secret") is None


def test_provider_config_completeness_rules() -> None:
    assert not OAuthProviderConfig(
        provider="google",
        client_id="",
        client_secret="secret",
    ).is_complete
    assert OAuthProviderConfig(
        provider="microsoft",
        client_id="client",
        client_secret="secret",
    ).is_complete
    assert OAuthProviderConfig(
        provider="oidc",
        client_id="client",
        client_secret="secret",
        discovery_url="https://issuer.example.com/.well-known/openid-configuration",
    ).is_complete
    assert not OAuthProviderConfig(
        provider="oidc",
        client_id="client",
        client_secret="secret",
    ).is_complete
    assert not OAuthProviderConfig(
        provider="unknown",  # type: ignore[arg-type]
        client_id="client",
        client_secret="secret",
    ).is_complete


@pytest.mark.asyncio
async def test_get_config_value_reads_value_json() -> None:
    db = _FakeDb([SimpleNamespace(value_json={"value": "client-id"})])
    service = OAuthConfigService(db=db)

    assert await service._get_config_value(OAUTH_GOOGLE_CLIENT_ID) == "client-id"


@pytest.mark.asyncio
async def test_get_config_value_returns_none_for_missing_or_empty() -> None:
    service = OAuthConfigService(db=_FakeDb([None, SimpleNamespace(value_json={})]))

    assert await service._get_config_value("missing") is None
    assert await service._get_config_value("empty") is None


@pytest.mark.asyncio
async def test_set_config_value_updates_existing_and_adds_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = SimpleNamespace(value_json={"value": "old"}, updated_by=None)
    db = _FakeDb([existing, None])
    service = OAuthConfigService(db=db)
    monkeypatch.setattr(oauth_config_service, "encrypt_secret", lambda value: f"enc:{value}")

    await service._set_config_value("client_id", "new", updated_by="operator")
    await service._set_config_value(
        "client_secret",
        "secret",
        is_secret=True,
        updated_by="operator",
    )

    assert existing.value_json == {"value": "new"}
    assert existing.updated_by == "operator"
    assert len(db.added) == 1
    assert db.added[0].key == "client_secret"
    assert db.added[0].value_json == {"value": "enc:secret"}
    assert db.added[0].created_by == "operator"
    assert db.flushes == 2


@pytest.mark.asyncio
async def test_delete_config_keys_executes_delete_and_flushes() -> None:
    db = _FakeDb()
    service = OAuthConfigService(db=db)

    await service._delete_config_keys(["a", "b"])

    assert len(db.executed) == 1
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_get_provider_config_builds_microsoft_google_and_oidc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = iter([
        "ms-client",
        "ms-secret",
        None,
        "google-client",
        "google-secret",
        "https://issuer.example.com/.well-known/openid-configuration",
        "oidc-client",
        "oidc-secret",
        None,
    ])
    service = OAuthConfigService(db=AsyncMock())
    service._get_config_value = AsyncMock(side_effect=lambda key: next(values))  # type: ignore[method-assign]
    service._get_secret_value = AsyncMock(side_effect=lambda key: next(values))  # type: ignore[method-assign]

    microsoft = await service.get_provider_config("microsoft")
    google = await service.get_provider_config("google")
    oidc = await service.get_provider_config("oidc")

    assert microsoft == OAuthProviderConfig(
        provider="microsoft",
        client_id="ms-client",
        client_secret="ms-secret",
        tenant_id="common",
    )
    assert google == OAuthProviderConfig(
        provider="google",
        client_id="google-client",
        client_secret="google-secret",
    )
    assert oidc == OAuthProviderConfig(
        provider="oidc",
        client_id="oidc-client",
        client_secret="oidc-secret",
        discovery_url="https://issuer.example.com/.well-known/openid-configuration",
        display_name="SSO",
    )


@pytest.mark.asyncio
async def test_get_provider_config_returns_none_for_incomplete_provider() -> None:
    service = OAuthConfigService(db=AsyncMock())
    service._get_config_value = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service._get_secret_value = AsyncMock(return_value=None)  # type: ignore[method-assign]

    assert await service.get_provider_config("microsoft") is None
    assert await service.get_provider_config("google") is None
    assert await service.get_provider_config("oidc") is None
    assert await service.get_provider_config("unknown") is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_set_provider_configs_write_expected_keys() -> None:
    service = OAuthConfigService(db=AsyncMock())
    calls = []

    async def set_value(key, value, is_secret=False, updated_by="system"):
        calls.append((key, value, is_secret, updated_by))

    service._set_config_value = set_value  # type: ignore[method-assign]

    await service.set_microsoft_config(
        MicrosoftOAuthConfigRequest(
            client_id="ms-client",
            client_secret="ms-secret",
            tenant_id="organizations",
        ),
        updated_by="operator",
    )
    await service.set_google_config(
        GoogleOAuthConfigRequest(client_id="google-client", client_secret="google-secret"),
        updated_by="operator",
    )
    await service.set_oidc_config(
        OIDCConfigRequest(
            discovery_url="https://issuer.example.com/.well-known/openid-configuration",
            client_id="oidc-client",
            client_secret="oidc-secret",
            display_name="Okta",
        ),
        updated_by="operator",
    )

    assert calls == [
        (OAUTH_MICROSOFT_CLIENT_ID, "ms-client", False, "operator"),
        (OAUTH_MICROSOFT_CLIENT_SECRET, "ms-secret", True, "operator"),
        (OAUTH_MICROSOFT_TENANT_ID, "organizations", False, "operator"),
        (OAUTH_GOOGLE_CLIENT_ID, "google-client", False, "operator"),
        (OAUTH_GOOGLE_CLIENT_SECRET, "google-secret", True, "operator"),
        (
            OAUTH_OIDC_DISCOVERY_URL,
            "https://issuer.example.com/.well-known/openid-configuration",
            False,
            "operator",
        ),
        (OAUTH_OIDC_CLIENT_ID, "oidc-client", False, "operator"),
        (OAUTH_OIDC_CLIENT_SECRET, "oidc-secret", True, "operator"),
        (OAUTH_OIDC_DISPLAY_NAME, "Okta", False, "operator"),
    ]


@pytest.mark.asyncio
async def test_delete_provider_config_selects_provider_keys() -> None:
    service = OAuthConfigService(db=AsyncMock())
    service.get_login_preference = AsyncMock(
        return_value=SimpleNamespace(
            auto_redirect_to_sso=False,
            default_sso_provider=None,
        )
    )
    deleted = []

    async def delete_keys(keys):
        deleted.append(keys)

    service._delete_config_keys = delete_keys  # type: ignore[method-assign]

    assert await service.delete_provider_config("microsoft") is True
    assert await service.delete_provider_config("google") is True
    assert await service.delete_provider_config("oidc") is True
    assert await service.delete_provider_config("unknown") is False  # type: ignore[arg-type]
    assert deleted == [
        [
            OAUTH_MICROSOFT_CLIENT_ID,
            OAUTH_MICROSOFT_CLIENT_SECRET,
            OAUTH_MICROSOFT_TENANT_ID,
        ],
        [OAUTH_GOOGLE_CLIENT_ID, OAUTH_GOOGLE_CLIENT_SECRET],
        [
            OAUTH_OIDC_DISCOVERY_URL,
            OAUTH_OIDC_CLIENT_ID,
            OAUTH_OIDC_CLIENT_SECRET,
            OAUTH_OIDC_DISPLAY_NAME,
        ],
    ]


@pytest.mark.asyncio
async def test_get_all_and_available_provider_configs() -> None:
    configs = {
        "microsoft": OAuthProviderConfig(
            provider="microsoft",
            client_id="ms-client",
            client_secret="secret",
            tenant_id="organizations",
        ),
        "google": None,
        "oidc": OAuthProviderConfig(
            provider="oidc",
            client_id="oidc-client",
            client_secret="secret",
            discovery_url="https://issuer.example.com/.well-known/openid-configuration",
            display_name="Okta",
        ),
    }
    service = OAuthConfigService(db=AsyncMock())

    async def get_provider(provider):
        return configs[provider]

    service.get_provider_config = get_provider  # type: ignore[method-assign]

    all_configs = await service.get_all_provider_configs()
    assert [item.provider for item in all_configs] == ["microsoft", "google", "oidc"]
    assert [item.configured for item in all_configs] == [True, False, True]
    assert all_configs[0].tenant_id == "organizations"
    assert all_configs[2].display_name == "Okta"
    assert await service.get_available_providers() == ["microsoft", "oidc"]


@pytest.mark.asyncio
async def test_test_provider_config_dispatches_to_provider_methods() -> None:
    service = OAuthConfigService(db=AsyncMock())
    service.test_microsoft_config = AsyncMock(return_value="microsoft")  # type: ignore[method-assign]
    service.test_google_config = AsyncMock(return_value="google")  # type: ignore[method-assign]
    service.test_oidc_config = AsyncMock(return_value="oidc")  # type: ignore[method-assign]

    assert await service.test_provider_config("microsoft", {"tenant_id": "common"}) == "microsoft"
    assert await service.test_provider_config("google", {"client_id": "id"}) == "google"
    assert await service.test_provider_config("oidc", {"discovery_url": "https://issuer"}) == "oidc"
    unknown = await service.test_provider_config("unknown", {})  # type: ignore[arg-type]
    assert unknown.success is False
    assert "Unknown provider" in unknown.message


@pytest.mark.asyncio
async def test_discovery_tests_return_success_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.error = None
    _FakeAsyncClient.response = _FakeResponse(
        payload={
            "issuer": "https://issuer.example.com",
            "authorization_endpoint": "https://issuer.example.com/auth",
            "token_endpoint": "https://issuer.example.com/token",
            "userinfo_endpoint": "https://issuer.example.com/userinfo",
            "scopes_supported": list("abcdefghijkl"),
        }
    )
    monkeypatch.setattr(oauth_config_service.httpx, "AsyncClient", _FakeAsyncClient)
    service = OAuthConfigService(db=AsyncMock())

    microsoft = await service.test_microsoft_config("id", "secret", "tenant")
    google = await service.test_google_config("id", "secret")
    oidc = await service.test_oidc_config("https://issuer.example.com/.well-known/openid-configuration", "id", "secret")

    assert microsoft.success is True
    assert google.success is True
    assert oidc.success is True
    assert oidc.details["scopes_supported"] == list("abcdefghij")
    assert _FakeAsyncClient.calls == [
        (
            "https://login.microsoftonline.com/tenant/v2.0/.well-known/openid-configuration",
            10.0,
        ),
        ("https://accounts.google.com/.well-known/openid-configuration", 10.0),
        ("https://issuer.example.com/.well-known/openid-configuration", 10.0),
    ]


@pytest.mark.asyncio
async def test_discovery_tests_return_not_configured_and_error_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = OAuthConfigService(db=AsyncMock())
    service.get_provider_config = AsyncMock(return_value=None)  # type: ignore[method-assign]

    assert (await service.test_microsoft_config()).message == "Microsoft OAuth is not configured"
    assert (await service.test_google_config()).message == "Google OAuth is not configured"
    assert (await service.test_oidc_config()).message == "OIDC is not configured"

    _FakeAsyncClient.calls = []
    _FakeAsyncClient.error = None
    _FakeAsyncClient.response = _FakeResponse(status_code=500)
    monkeypatch.setattr(oauth_config_service.httpx, "AsyncClient", _FakeAsyncClient)

    assert (await service.test_google_config("id", "secret")).success is False
    assert "HTTP 500" in (await service.test_oidc_config("https://issuer", "id", "secret")).message
    assert "HTTP 500" in (await service.test_microsoft_config("id", "secret")).message

    _FakeAsyncClient.error = httpx.RequestError("offline")
    assert "Network error" in (await service.test_microsoft_config("id", "secret")).message
    assert "Network error" in (await service.test_google_config("id", "secret")).message


@pytest.mark.asyncio
async def test_oidc_discovery_reports_missing_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeAsyncClient.error = None
    _FakeAsyncClient.response = _FakeResponse(payload={"issuer": "https://issuer.example.com"})
    monkeypatch.setattr(oauth_config_service.httpx, "AsyncClient", _FakeAsyncClient)

    response = await OAuthConfigService(db=AsyncMock()).test_oidc_config(
        "https://issuer.example.com/.well-known/openid-configuration",
        "id",
        "secret",
    )

    assert response.success is False
    assert "missing required fields" in response.message


@pytest.mark.asyncio
async def test_discovery_tests_use_saved_configs_for_fallback_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.error = None
    _FakeAsyncClient.response = _FakeResponse(
        payload={
            "issuer": "https://issuer.example.com",
            "authorization_endpoint": "https://issuer.example.com/auth",
            "token_endpoint": "https://issuer.example.com/token",
        }
    )
    monkeypatch.setattr(oauth_config_service.httpx, "AsyncClient", _FakeAsyncClient)
    service = OAuthConfigService(db=AsyncMock())
    configs = {
        "microsoft": OAuthProviderConfig(
            provider="microsoft",
            client_id="ms-client",
            client_secret="secret",
            tenant_id="organizations",
        ),
        "oidc": OAuthProviderConfig(
            provider="oidc",
            client_id="oidc-client",
            client_secret="secret",
            discovery_url="https://issuer.example.com/.well-known/openid-configuration",
        ),
    }
    service.get_provider_config = AsyncMock(side_effect=lambda provider: configs.get(provider))  # type: ignore[method-assign]

    microsoft = await service.test_microsoft_config()
    oidc = await service.test_oidc_config(client_id="id", client_secret="secret")

    assert microsoft.success is True
    assert oidc.success is True
    assert _FakeAsyncClient.calls[0][0] == (
        "https://login.microsoftonline.com/organizations/v2.0/.well-known/openid-configuration"
    )
    assert _FakeAsyncClient.calls[1][0] == (
        "https://issuer.example.com/.well-known/openid-configuration"
    )


@pytest.mark.asyncio
async def test_oidc_fallback_reports_missing_discovery_url() -> None:
    service = OAuthConfigService(db=AsyncMock())
    service.get_provider_config = AsyncMock(
        return_value=OAuthProviderConfig(
            provider="oidc",
            client_id="id",
            client_secret="secret",
            discovery_url=None,
        )
    )  # type: ignore[method-assign]

    response = await service.test_oidc_config(client_id="id", client_secret="secret")

    assert response.success is False
    assert response.message == "OIDC discovery URL is required"
