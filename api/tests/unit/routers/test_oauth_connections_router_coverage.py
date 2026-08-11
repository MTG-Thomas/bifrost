"""Focused unit coverage for the OAuth connections router."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.oauth import UpdateOAuthConnectionRequest
from src.routers import oauth_connections


def _ctx(*, org_id=None):
    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()
    return SimpleNamespace(
        org_id=org_id,
        db=db,
        user=SimpleNamespace(
            email="admin@example.com",
            user_id=uuid4(),
            name="Admin User",
        ),
    )


def _repo(**methods):
    repo = MagicMock()
    for name, value in methods.items():
        setattr(repo, name, AsyncMock(return_value=value))
    return repo


def _provider(**overrides):
    data = {
        "id": uuid4(),
        "provider_name": "halo",
        "display_name": "Halo",
        "description": None,
        "oauth_flow_type": "authorization_code",
        "client_id": "client-id",
        "encrypted_client_secret": b"encrypted-secret",
        "authorization_url": "https://auth.example.test/oauth/authorize",
        "token_url": "https://auth.example.test/oauth/token",
        "scopes": ["openid", "offline_access"],
        "audience": None,
        "provider_metadata": {},
        "status": "completed",
        "status_message": None,
        "integration_id": uuid4(),
        "organization_id": None,
        "entity_id_source": None,
        "created_by": "admin@example.com",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _detail(connection_name="halo"):
    now = datetime.now(timezone.utc)
    return {
        "connection_name": connection_name,
        "name": "Halo",
        "provider": connection_name,
        "description": None,
        "oauth_flow_type": "authorization_code",
        "client_id": "client-id",
        "authorization_url": "https://auth.example.test/oauth/authorize",
        "token_url": "https://auth.example.test/oauth/token",
        "scopes": "openid offline_access",
        "audience": None,
        "provider_metadata": {},
        "status": "completed",
        "status_message": None,
        "integration_id": str(uuid4()),
        "expires_at": None,
        "last_refresh_at": None,
        "last_test_at": None,
        "created_at": now,
        "created_by": "admin@example.com",
        "updated_at": now,
    }


@pytest.mark.asyncio
async def test_get_connection_returns_repository_detail():
    provider = _provider()
    detail = _detail()
    repo = _repo(get_by_connection_name=provider, to_detail=detail)

    with patch.object(oauth_connections, "OAuthProviderRepository", return_value=repo):
        result = await oauth_connections.get_connection("halo", _ctx(), MagicMock())

    assert result == detail
    repo.get_by_connection_name.assert_awaited_once_with("halo")
    repo.to_detail.assert_awaited_once_with(provider)


@pytest.mark.asyncio
async def test_update_connection_404_when_repo_cannot_find_connection():
    repo = _repo(update_connection=None)
    request = UpdateOAuthConnectionRequest(name="New Name")

    with (
        patch.object(oauth_connections, "OAuthProviderRepository", return_value=repo),
        pytest.raises(HTTPException) as exc_info,
    ):
        await oauth_connections.update_connection("missing", request, _ctx(), MagicMock())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "OAuth connection 'missing' not found"


@pytest.mark.asyncio
async def test_delete_connection_invalidates_cache_after_successful_delete():
    org_id = uuid4()
    repo = _repo(delete_connection=True)

    with (
        patch.object(oauth_connections, "OAuthProviderRepository", return_value=repo),
        patch.object(oauth_connections, "CACHE_INVALIDATION_AVAILABLE", True),
        patch.object(oauth_connections, "invalidate_oauth", new=AsyncMock()) as invalidate,
    ):
        result = await oauth_connections.delete_connection("halo", _ctx(org_id=org_id), MagicMock())

    assert result is None
    repo.delete_connection.assert_awaited_once_with("halo")
    invalidate.assert_awaited_once_with(str(org_id), "halo")


@pytest.mark.asyncio
async def test_authorize_connection_resolves_url_and_marks_waiting():
    provider = _provider(scopes=["read", "write"])
    repo = _repo(get_by_connection_name=provider, update_status=None)
    ctx = _ctx()

    with (
        patch.object(oauth_connections, "OAuthProviderRepository", return_value=repo),
        patch.object(oauth_connections.secrets, "token_urlsafe", return_value="state-token"),
        patch.object(
            oauth_connections,
            "get_url_resolution_defaults",
            new=AsyncMock(return_value={"tenant": "contoso"}),
        ) as defaults,
        patch.object(
            oauth_connections,
            "resolve_url_template",
            return_value="https://auth.example.test/contoso/authorize",
        ) as resolve,
    ):
        result = await oauth_connections.authorize_connection(
            "halo",
            ctx,
            MagicMock(),
            redirect_uri="https://app.example.test/oauth/callback",
        )

    assert result.state == "state-token"
    assert result.authorization_url.startswith("https://auth.example.test/contoso/authorize?")
    assert "scope=read+write" in result.authorization_url
    defaults.assert_awaited_once_with(ctx.db, provider)
    resolve.assert_called_once_with(
        url=provider.authorization_url,
        defaults={"tenant": "contoso"},
    )
    repo.update_status.assert_awaited_once_with(
        connection_name="halo",
        status="waiting_callback",
        status_message="Waiting for user to complete authorization",
    )


@pytest.mark.asyncio
async def test_authorize_connection_can_omit_scope_parameter():
    provider = _provider(
        scopes=["read", "write"],
        provider_metadata={"omit_authorization_scope": True},
    )
    repo = _repo(get_by_connection_name=provider, update_status=None)

    with (
        patch.object(oauth_connections, "OAuthProviderRepository", return_value=repo),
        patch.object(oauth_connections.secrets, "token_urlsafe", return_value="state-token"),
        patch.object(
            oauth_connections,
            "get_url_resolution_defaults",
            new=AsyncMock(return_value={}),
        ),
    ):
        result = await oauth_connections.authorize_connection(
            "halo",
            _ctx(),
            MagicMock(),
            redirect_uri="https://app.example.test/oauth/callback",
        )

    assert "scope=" not in result.authorization_url
    assert "client_id=client-id" in result.authorization_url
    assert "redirect_uri=https%3A%2F%2Fapp.example.test%2Foauth%2Fcallback" in result.authorization_url


@pytest.mark.asyncio
async def test_authorize_connection_rejects_client_credentials_connection():
    provider = _provider(authorization_url=None)
    repo = _repo(get_by_connection_name=provider)

    with (
        patch.object(oauth_connections, "OAuthProviderRepository", return_value=repo),
        pytest.raises(HTTPException) as exc_info,
    ):
        await oauth_connections.authorize_connection(
            "halo",
            _ctx(),
            MagicMock(),
            redirect_uri="https://app.example.test/oauth/callback",
        )

    assert exc_info.value.status_code == 400
    assert "client_credentials flow" in exc_info.value.detail


@pytest.mark.asyncio
async def test_cancel_authorization_resets_status_and_returns_refreshed_detail():
    provider = _provider(status="waiting_callback")
    detail = _detail()
    repo = _repo(get_by_connection_name=provider, update_status=None, to_detail=detail)
    ctx = _ctx()

    with patch.object(oauth_connections, "OAuthProviderRepository", return_value=repo):
        result = await oauth_connections.cancel_authorization("halo", ctx, MagicMock())

    assert result == detail
    repo.update_status.assert_awaited_once_with(
        connection_name="halo",
        status="not_connected",
        status_message="Authorization cancelled",
    )
    ctx.db.refresh.assert_awaited_once_with(provider)


@pytest.mark.asyncio
async def test_refresh_token_returns_failure_response_and_marks_provider_failed():
    provider = _provider(token_url="https://auth.example.test/token")
    repo = _repo(
        get_by_connection_name=provider,
        get_token=SimpleNamespace(encrypted_refresh_token=b"r", organization_id=uuid4()),
    )
    ctx = _ctx()

    with (
        patch.object(oauth_connections, "OAuthProviderRepository", return_value=repo),
        patch.object(
            oauth_connections,
            "build_token_refresh_context",
            new=AsyncMock(return_value={"provider_name": "halo"}),
        ),
        patch.object(
            oauth_connections,
            "refresh_oauth_token_http",
            new=AsyncMock(return_value={"success": False, "error": "invalid_grant"}),
        ),
    ):
        result = await oauth_connections.refresh_token("halo", ctx, MagicMock())

    assert result.success is False
    assert result.message == "invalid_grant"
    assert provider.status == "failed"
    assert provider.status_message == "invalid_grant"
    ctx.db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_credentials_without_token_returns_status_only_response():
    provider = _provider(status="not_connected")
    repo = _repo(get_by_connection_name=provider, get_token=None)

    with patch.object(oauth_connections, "OAuthProviderRepository", return_value=repo):
        result = await oauth_connections.get_credentials("halo", _ctx(), MagicMock())

    assert result.connection_name == "halo"
    assert result.credentials is None
    assert result.status == "not_connected"
    assert result.integration_id == str(provider.integration_id)
    assert result.expires_at is None


@pytest.mark.asyncio
async def test_get_refresh_job_status_builds_last_run_from_platform_job():
    job_data = {
        "start_time": "2026-07-05T07:00:00+00:00",
        "end_time": "2026-07-05T07:01:00+00:00",
        "total_connections": 5,
        "refreshed_successfully": 3,
        "refresh_failed": 1,
        "needs_refresh": 4,
        "errors": ["halo failed"],
    }
    started_at = datetime(2026, 7, 5, 7, 0, tzinfo=timezone.utc)
    completed_at = datetime(2026, 7, 5, 7, 1, tzinfo=timezone.utc)
    result = MagicMock()
    result.scalar_one_or_none.return_value = SimpleNamespace(
        result=job_data,
        status="succeeded",
        started_at=started_at,
        completed_at=completed_at,
        error_message=None,
    )
    ctx = _ctx()
    ctx.db.execute.return_value = result

    response = await oauth_connections.get_refresh_job_status(ctx, MagicMock())

    assert response.enabled is True
    assert response.last_run is not None
    assert response.last_run.status == "succeeded"
    assert response.last_run.start_time == started_at
    assert response.last_run.end_time == completed_at
    assert response.last_run.connections_checked == 5
    assert response.last_run.refreshed_successfully == 3
    assert response.last_run.errors == ["halo failed"]


@pytest.mark.asyncio
async def test_trigger_refresh_all_enqueues_platform_job():
    ctx = _ctx()
    job = SimpleNamespace(id=uuid4(), status="queued")

    with (
        patch.object(
            oauth_connections,
            "enqueue_platform_job",
            AsyncMock(return_value=(job, False)),
        ) as enqueue,
        patch.object(
            oauth_connections,
            "publish_platform_job_update",
            AsyncMock(),
        ) as publish,
    ):
        response = await oauth_connections.trigger_refresh_all(ctx, ctx.user)

    assert response.job_id == job.id
    assert response.status.value == "queued"
    assert response.reused is False
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["dedupe_key"] == "manual"
    assert enqueue.await_args.kwargs["resource_lock_key"] == "oauth.refresh"
    ctx.db.commit.assert_awaited_once()
    publish.assert_awaited_once_with(job)
