from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models import OAuthProvider, OAuthToken, UpdateOAuthConnectionRequest
from src.services import oauth_storage
from src.services.oauth_storage import OAuthStorageService


class _FakeScalars:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def scalars(self):
        return _FakeScalars(self._row)


class _FakeDb:
    def __init__(self, rows):
        self._rows = list(rows)
        self.added = []
        self.deleted = []
        self.executed = []
        self.commits = 0
        self.refreshed = []

    async def execute(self, statement):
        self.executed.append(statement)
        return _FakeResult(self._rows.pop(0) if self._rows else None)

    def add(self, row):
        self.added.append(row)

    def delete(self, row):
        self.deleted.append(row)

    async def commit(self):
        self.commits += 1

    async def refresh(self, row):
        self.refreshed.append(row)


def _provider(**overrides):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    values = {
        "id": uuid4(),
        "organization_id": None,
        "provider_name": "halo",
        "provider_metadata": {
            "authorization_url": "https://auth.example.com/authorize",
            "token_url": "https://auth.example.com/token",
            "redirect_uri": "/oauth/callback/halo",
            "oauth_flow_type": "authorization_code",
            "description": "Halo",
            "created_by": "admin@example.com",
            "status": "not_connected",
        },
        "client_id": "client-id",
        "encrypted_client_secret": b"secret",
        "token_url_defaults": {},
        "scopes": ["read"],
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return OAuthProvider(**values)


def _token(**overrides):
    values = {
        "id": uuid4(),
        "organization_id": None,
        "provider_id": uuid4(),
        "user_id": None,
        "encrypted_access_token": b"access",
        "encrypted_refresh_token": b"refresh",
        "expires_at": datetime(2026, 2, 1, tzinfo=timezone.utc),
        "scopes": ["read"],
    }
    values.update(overrides)
    return OAuthToken(**values)


def test_encrypt_decrypt_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        oauth_storage,
        "_get_encryption_key",
        lambda: b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    )

    encrypted = oauth_storage._encrypt("secret-token")

    assert encrypted != b"secret-token"
    assert oauth_storage._decrypt(encrypted) == "secret-token"


@pytest.mark.asyncio
async def test_get_session_context_uses_injected_session() -> None:
    session = object()
    service = OAuthStorageService(session=session)

    async with service._get_session_context() as yielded:
        assert yielded is session


def test_to_connection_model_maps_provider_metadata_defaults() -> None:
    org_id = uuid4()
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    updated_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    provider = SimpleNamespace(
        organization_id=org_id,
        provider_name="halo",
        provider_metadata={
            "description": "Halo OAuth",
            "oauth_flow_type": "authorization_code",
            "authorization_url": "https://auth.example.com/authorize",
            "token_url": "https://auth.example.com/token",
            "redirect_uri": "/oauth/callback/custom",
            "status": "connected",
            "created_by": "admin@example.com",
        },
        client_id="client-id",
        token_url_defaults={"tenant": "midtown"},
        scopes=["read", "write"],
        created_at=created_at,
        updated_at=updated_at,
    )

    connection = OAuthStorageService()._to_connection_model(provider)

    assert connection.org_id == str(org_id)
    assert connection.connection_name == "halo"
    assert connection.description == "Halo OAuth"
    assert connection.oauth_flow_type == "authorization_code"
    assert connection.client_id == "client-id"
    assert connection.client_secret_config_key == "oauth_halo_client_secret"
    assert connection.oauth_response_config_key == "oauth_halo_oauth_response"
    assert connection.authorization_url == "https://auth.example.com/authorize"
    assert connection.token_url == "https://auth.example.com/token"
    assert connection.token_url_defaults == {"tenant": "midtown"}
    assert connection.scopes == "read,write"
    assert connection.redirect_uri == "/oauth/callback/custom"
    assert connection.status == "connected"
    assert connection.created_by == "admin@example.com"
    assert connection.created_at == created_at
    assert connection.updated_at == updated_at


def test_to_connection_model_handles_global_provider_defaults() -> None:
    provider = SimpleNamespace(
        organization_id=None,
        provider_name="generic",
        provider_metadata={},
        client_id="client-id",
        token_url_defaults=None,
        scopes=[],
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    connection = OAuthStorageService()._to_connection_model(provider)

    assert connection.org_id == "GLOBAL"
    assert connection.description is None
    assert connection.oauth_flow_type == "authorization_code"
    assert connection.authorization_url is None
    assert connection.token_url == "https://oauth.example.com/token"
    assert connection.token_url_defaults == {}
    assert connection.scopes == ""
    assert connection.redirect_uri == "/oauth/callback/generic"
    assert connection.status == "not_connected"
    assert connection.created_by == "system"


@pytest.mark.asyncio
async def test_get_connection_returns_org_specific_or_global_provider() -> None:
    provider = _provider()
    service = OAuthStorageService(session=_FakeDb([provider]))

    connection = await service.get_connection(str(uuid4()), "halo")

    assert connection is not None
    assert connection.connection_name == "halo"
    assert connection.client_id == "client-id"


@pytest.mark.asyncio
async def test_get_connection_returns_none_when_missing() -> None:
    service = OAuthStorageService(session=_FakeDb([None]))

    assert await service.get_connection("GLOBAL", "missing") is None


@pytest.mark.asyncio
async def test_get_by_integration_id_returns_connection() -> None:
    integration_id = uuid4()
    service = OAuthStorageService(session=_FakeDb([_provider(integration_id=integration_id)]))

    connection = await service.get_by_integration_id(str(integration_id))

    assert connection is not None
    assert connection.connection_name == "halo"


@pytest.mark.asyncio
async def test_update_connection_updates_mutable_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider(provider_metadata={"authorization_url": "https://old.example.com/auth"})
    db = _FakeDb([provider])
    service = OAuthStorageService(session=db)
    monkeypatch.setattr(oauth_storage, "_encrypt", lambda value: f"encrypted:{value}".encode())

    connection = await service.update_connection(
        "GLOBAL",
        "halo",
        UpdateOAuthConnectionRequest(
            client_id="new-client",
            client_secret="new-secret",
            scopes="read write",
            authorization_url="https://new.example.com/auth",
            token_url="https://new.example.com/token",
            token_url_defaults={"tenant": "midtown"},
        ),
        "operator@example.com",
    )

    assert connection is not None
    assert provider.client_id == "new-client"
    assert provider.encrypted_client_secret == b"encrypted:new-secret"
    assert provider.scopes == ["read", "write"]
    assert provider.provider_metadata["authorization_url"] == "https://new.example.com/auth"
    assert provider.provider_metadata["token_url"] == "https://new.example.com/token"
    assert provider.provider_metadata["updated_by"] == "operator@example.com"
    assert provider.token_url_defaults == {"tenant": "midtown"}
    assert db.commits == 1
    assert db.refreshed == [provider]


@pytest.mark.asyncio
async def test_update_connection_returns_none_when_missing() -> None:
    service = OAuthStorageService(session=_FakeDb([None]))

    assert (
        await service.update_connection(
            "GLOBAL",
            "missing",
            UpdateOAuthConnectionRequest(client_id="new-client"),
            "operator@example.com",
        )
        is None
    )


@pytest.mark.asyncio
async def test_delete_connection_removes_tokens_then_provider() -> None:
    provider = _provider()
    db = _FakeDb([provider, None])
    service = OAuthStorageService(session=db)

    assert await service.delete_connection("GLOBAL", "halo") is True

    assert len(db.executed) == 2
    assert db.deleted == [provider]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_delete_connection_returns_false_when_missing() -> None:
    service = OAuthStorageService(session=_FakeDb([None]))

    assert await service.delete_connection("GLOBAL", "missing") is False


@pytest.mark.asyncio
async def test_store_tokens_updates_existing_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expires_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
    provider = _provider(provider_metadata={"status": "not_connected"})
    token = _token(provider_id=provider.id)
    db = _FakeDb([provider, token])
    service = OAuthStorageService(session=db)
    monkeypatch.setattr(oauth_storage, "_encrypt", lambda value: f"encrypted:{value}".encode())

    assert (
        await service.store_tokens(
            "GLOBAL",
            "halo",
            "access-new",
            refresh_token="refresh-new",
            expires_at=expires_at,
            scopes=["read", "write"],
        )
        is True
    )

    assert token.encrypted_access_token == b"encrypted:access-new"
    assert token.encrypted_refresh_token == b"encrypted:refresh-new"
    assert token.expires_at == expires_at
    assert token.scopes == ["read", "write"]
    assert provider.provider_metadata["status"] == "connected"
    assert db.added == []
    assert db.commits == 1


@pytest.mark.asyncio
async def test_store_tokens_creates_token_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider(provider_metadata={})
    db = _FakeDb([provider, None])
    service = OAuthStorageService(session=db)
    monkeypatch.setattr(oauth_storage, "_encrypt", lambda value: f"encrypted:{value}".encode())

    assert await service.store_tokens("GLOBAL", "halo", "access", scopes=None) is True

    assert len(db.added) == 1
    assert db.added[0].provider_id == provider.id
    assert db.added[0].encrypted_access_token == b"encrypted:access"
    assert db.added[0].encrypted_refresh_token is None
    assert db.added[0].scopes == []
    assert provider.provider_metadata["status"] == "connected"


@pytest.mark.asyncio
async def test_store_tokens_returns_false_when_provider_missing() -> None:
    service = OAuthStorageService(session=_FakeDb([None]))

    assert await service.store_tokens("GLOBAL", "missing", "access") is False


@pytest.mark.asyncio
async def test_get_tokens_decrypts_stored_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expires_at = datetime(2026, 4, 1, tzinfo=timezone.utc)
    provider = _provider()
    token = _token(
        provider_id=provider.id,
        encrypted_access_token=b"encrypted-access",
        encrypted_refresh_token=b"encrypted-refresh",
        expires_at=expires_at,
        scopes=["read"],
    )
    service = OAuthStorageService(session=_FakeDb([provider, token]))
    monkeypatch.setattr(oauth_storage, "_decrypt", lambda value: f"decrypted:{value.decode()}")

    assert await service.get_tokens("GLOBAL", "halo") == {
        "access_token": "decrypted:encrypted-access",
        "refresh_token": "decrypted:encrypted-refresh",
        "expires_at": expires_at.isoformat(),
        "scopes": ["read"],
    }


@pytest.mark.asyncio
async def test_get_tokens_returns_none_when_provider_or_token_missing() -> None:
    assert await OAuthStorageService(session=_FakeDb([None])).get_tokens("GLOBAL", "halo") is None
    assert (
        await OAuthStorageService(session=_FakeDb([_provider(), None])).get_tokens(
            "GLOBAL",
            "halo",
        )
        is None
    )
