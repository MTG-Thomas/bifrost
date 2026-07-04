from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services import oauth_storage
from src.services.oauth_storage import OAuthStorageService


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
