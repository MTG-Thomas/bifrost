from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models import MappingAuthorizeRequest
from src.routers import integrations


INTEGRATION_ID = UUID("11111111-1111-1111-1111-111111111111")
MAPPING_ID = UUID("22222222-2222-2222-2222-222222222222")
PROVIDER_ID = UUID("33333333-3333-3333-3333-333333333333")
ORG_ID = UUID("44444444-4444-4444-4444-444444444444")
TOKEN_ID = UUID("55555555-5555-5555-5555-555555555555")
USER_ID = UUID("66666666-6666-6666-6666-666666666666")
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _ctx(db):
    return SimpleNamespace(
        db=db,
        user=UserPrincipal(
            user_id=USER_ID,
            email="admin@example.test",
            name="Admin User",
            organization_id=None,
            is_superuser=True,
        ),
    )


def _mapping(*, oauth_token_id: UUID | None = TOKEN_ID):
    return SimpleNamespace(
        id=MAPPING_ID,
        integration_id=INTEGRATION_ID,
        organization_id=ORG_ID,
        entity_id="tenant-123",
        entity_name="Tenant 123",
        oauth_token_id=oauth_token_id,
        integration=SimpleNamespace(id=INTEGRATION_ID, name="Acme API"),
        organization=SimpleNamespace(id=ORG_ID, name="Acme"),
        created_at=NOW,
        updated_at=NOW,
    )


class FakeDb:
    def __init__(self, token=None):
        self.token = token
        self.flush_count = 0
        self.deleted = []
        self.get_calls = []

    async def flush(self):
        self.flush_count += 1

    async def get(self, model, item_id):
        self.get_calls.append((model, item_id))
        return self.token if item_id == TOKEN_ID else None

    async def delete(self, row):
        self.deleted.append(row)


class FakeRepo:
    def __init__(self, db, *, integration=None, mapping=None):
        self.db = db
        self.integration = integration
        self.mapping = mapping

    async def get_integration_by_id(self, integration_id):
        assert integration_id == INTEGRATION_ID
        return self.integration

    async def get_mapping_by_id(self, integration_id, mapping_id):
        assert integration_id == INTEGRATION_ID
        assert mapping_id == MAPPING_ID
        return self.mapping


def _patch_repo(monkeypatch, *, integration=None, mapping=None):
    monkeypatch.setattr(
        integrations,
        "IntegrationsRepository",
        lambda db: FakeRepo(db, integration=integration, mapping=mapping),
    )


@pytest.mark.asyncio
async def test_authorize_mapping_resolves_provider_url_and_remembers_signed_state(
    monkeypatch,
):
    provider = SimpleNamespace(
        id=PROVIDER_ID,
        authorization_url="https://auth.example.test/{tenant}/authorize",
        client_id="client-123",
        scopes=["read", "write"],
    )
    integration = SimpleNamespace(id=INTEGRATION_ID, oauth_provider=provider)
    db = FakeDb()
    _patch_repo(monkeypatch, integration=integration, mapping=_mapping())

    remembered_nonces = []
    monkeypatch.setattr(
        integrations,
        "get_url_resolution_defaults",
        lambda db_arg, provider_arg: _async_value({"tenant": "midtown"}),
    )
    monkeypatch.setattr(
        integrations,
        "resolve_url_template",
        lambda *, url, defaults: url.replace("{tenant}", defaults["tenant"]),
    )
    monkeypatch.setattr(
        integrations,
        "encode_state",
        lambda *, provider_id, mapping_id: ("signed-state", "nonce-1"),
    )
    monkeypatch.setattr(
        integrations,
        "remember_nonce",
        lambda nonce: _async_append(remembered_nonces, nonce),
    )

    response = await integrations.authorize_mapping(
        INTEGRATION_ID,
        MAPPING_ID,
        MappingAuthorizeRequest(redirect_uri="https://app.example.test/callback"),
        _ctx(db),
        SimpleNamespace(user_id=USER_ID),
    )

    assert response.authorization_url.startswith(
        "https://auth.example.test/midtown/authorize?"
    )
    assert "client_id=client-123" in response.authorization_url
    assert "response_type=code" in response.authorization_url
    assert "state=signed-state" in response.authorization_url
    assert "scope=read+write" in response.authorization_url
    assert "redirect_uri=https%3A%2F%2Fapp.example.test%2Fcallback" in (
        response.authorization_url
    )
    assert remembered_nonces == ["nonce-1"]


@pytest.mark.asyncio
async def test_disconnect_mapping_clears_token_deletes_row_and_ignores_event_failure(
    monkeypatch,
):
    token = SimpleNamespace(id=TOKEN_ID)
    db = FakeDb(token=token)
    mapping = _mapping(oauth_token_id=TOKEN_ID)
    _patch_repo(monkeypatch, mapping=mapping)

    async def failing_emit(**_kwargs):
        raise RuntimeError("event bus unavailable")

    monkeypatch.setattr(
        "src.services.events.builtins.emit_integration_disconnected",
        failing_emit,
    )

    await integrations.disconnect_mapping(
        INTEGRATION_ID,
        MAPPING_ID,
        _ctx(db),
        SimpleNamespace(user_id=USER_ID),
    )

    assert mapping.oauth_token_id is None
    assert db.deleted == [token]
    assert db.flush_count == 2


@pytest.mark.asyncio
async def test_refresh_mapping_oauth_clears_dangling_token_link(monkeypatch):
    mapping = _mapping(oauth_token_id=TOKEN_ID)
    db = FakeDb(token=None)
    _patch_repo(monkeypatch, mapping=mapping)

    with pytest.raises(HTTPException) as exc_info:
        await integrations.refresh_mapping_oauth(
            INTEGRATION_ID,
            MAPPING_ID,
            _ctx(db),
            SimpleNamespace(user_id=USER_ID),
        )

    assert exc_info.value.status_code == 404
    assert "mapping link cleared" in exc_info.value.detail
    assert mapping.oauth_token_id is None
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_refresh_mapping_oauth_marks_failed_token_and_reports_gateway_error(
    monkeypatch,
):
    previous_success_at = NOW - timedelta(hours=1)
    token = SimpleNamespace(
        id=TOKEN_ID,
        encrypted_refresh_token="refresh-token",
        encrypted_access_token="old-access",
        expires_at=NOW,
        scopes=["old"],
        status="completed",
        status_message=None,
        last_refresh_at=previous_success_at,
    )
    provider = SimpleNamespace(id=PROVIDER_ID, oauth_flow_type="authorization_code")
    integration = SimpleNamespace(
        id=INTEGRATION_ID,
        name="Acme API",
        oauth_provider=provider,
    )
    mapping = _mapping(oauth_token_id=TOKEN_ID)
    db = FakeDb(token=token)
    _patch_repo(monkeypatch, integration=integration, mapping=mapping)

    monkeypatch.setattr(
        "src.services.oauth_provider.build_token_refresh_context",
        lambda **kwargs: _async_value({"token": kwargs["token"]}),
    )
    monkeypatch.setattr(
        "src.services.oauth_provider.refresh_oauth_token_http",
        lambda _context: _async_value(
            {
                "success": False,
                "error": "refresh token expired and needs reconnect",
                "error_code": "invalid_grant",
            }
        ),
    )
    monkeypatch.setattr(
        integrations,
        "_emit_refresh_failed",
        lambda *args, **kwargs: _async_value(None),
    )

    with pytest.raises(HTTPException) as exc_info:
        await integrations.refresh_mapping_oauth(
            INTEGRATION_ID,
            MAPPING_ID,
            _ctx(db),
            SimpleNamespace(user_id=USER_ID),
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "refresh token expired and needs reconnect"
    assert token.status == "failed"
    assert token.status_message == "refresh token expired and needs reconnect"
    assert token.last_refresh_at > previous_success_at
    assert db.flush_count == 1


async def _async_value(value):
    return value


async def _async_append(values, value):
    values.append(value)
