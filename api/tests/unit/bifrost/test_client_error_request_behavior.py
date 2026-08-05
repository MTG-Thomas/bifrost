from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from bifrost import client as client_mod


@pytest.fixture(autouse=True)
def _reset_client_globals():
    client_mod._clear_client()
    client_mod._reset_refresh_coordinators_for_tests()
    if hasattr(client_mod._thread_local, "bifrost_client"):
        delattr(client_mod._thread_local, "bifrost_client")
    yield
    client_mod._clear_client()
    client_mod._reset_refresh_coordinators_for_tests()
    if hasattr(client_mod._thread_local, "bifrost_client"):
        delattr(client_mod._thread_local, "bifrost_client")


@pytest.mark.asyncio
async def test_request_supports_delete_body_and_refresh_retry(monkeypatch):
    first = AsyncMock(
        return_value=httpx.Response(
            401,
            request=httpx.Request("DELETE", "https://api.example.test/api/items/1"),
        )
    )
    second = AsyncMock(
        return_value=httpx.Response(
            204,
            request=httpx.Request("DELETE", "https://api.example.test/api/items/1"),
        )
    )
    refresh = AsyncMock(return_value=True)
    client = client_mod.BifrostClient("https://api.example.test", "old-token")
    clients = iter(
        [
            type("HTTP", (), {"request": first})(),
            type("HTTP", (), {"request": second})(),
        ]
    )
    monkeypatch.setattr(client, "_get_async_client", lambda: next(clients))
    monkeypatch.setattr(client, "_refresh_and_update", refresh)

    response = await client.request(
        "delete",
        "/api/items/1",
        json={"reason": "duplicate"},
    )

    assert response.status_code == 204
    first.assert_awaited_once_with(
        "DELETE",
        "/api/items/1",
        json={"reason": "duplicate"},
    )
    refresh.assert_awaited_once_with("old-token")
    second.assert_awaited_once_with(
        "DELETE",
        "/api/items/1",
        json={"reason": "duplicate"},
    )
    await client.close()


@pytest.mark.asyncio
async def test_refresh_tokens_returns_false_without_stored_credentials(monkeypatch):
    monkeypatch.setattr(client_mod, "get_credentials", lambda *_args, **_kwargs: None)

    assert await client_mod.refresh_tokens() is False


@pytest.mark.asyncio
async def test_refresh_tokens_returns_false_for_non_200_refresh_response(monkeypatch):
    monkeypatch.setattr(
        client_mod,
        "get_credentials",
        lambda *_args, **_kwargs: {
            "api_url": "https://api.example.test",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": "already-expired",
        },
    )
    refresh = AsyncMock(return_value=None)
    monkeypatch.setattr(client_mod, "refresh_connection_access_token", refresh)

    assert await client_mod.refresh_tokens() is False
    refresh.assert_awaited_once_with("https://api.example.test", "old-access")


@pytest.mark.asyncio
async def test_refresh_tokens_delegates_successful_refresh(monkeypatch):
    monkeypatch.setattr(
        client_mod,
        "get_credentials",
        lambda *_args, **_kwargs: {
            "api_url": "https://api.example.test/",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": "already-expired",
        },
    )
    refresh = AsyncMock(return_value="new-access")
    monkeypatch.setattr(client_mod, "refresh_connection_access_token", refresh)

    assert await client_mod.refresh_tokens() is True
    refresh.assert_awaited_once_with("https://api.example.test/", "old-access")


@pytest.mark.asyncio
async def test_get_instance_require_auth_does_not_login_inside_running_loop(monkeypatch):
    login = AsyncMock(return_value=True)
    monkeypatch.setattr(client_mod, "get_credentials", lambda **_kwargs: None)
    monkeypatch.setattr(client_mod, "is_token_expired", lambda: False)
    monkeypatch.setattr(client_mod, "login_flow", login)
    monkeypatch.setattr(
        client_mod,
        "resolve_current_connection",
        lambda **_kwargs: (None, None),
    )
    monkeypatch.setattr("bifrost.credentials.list_credentials", lambda: [])

    with pytest.raises(RuntimeError, match="Not logged in"):
        client_mod.BifrostClient.get_instance(require_auth=True)

    login.assert_not_called()


def test_get_instance_refresh_failure_clears_expired_credentials(monkeypatch):
    monkeypatch.setattr(
        client_mod,
        "get_credentials",
        lambda **_kwargs: {
            "api_url": "https://api.example.test",
            "access_token": "expired-access",
            "refresh_token": "refresh",
            "expires_at": "already-expired",
        },
    )
    monkeypatch.setattr(client_mod, "is_token_expired", lambda: True)
    refresh = MagicMock(return_value=None)
    monkeypatch.setattr(client_mod, "_refresh_connection_access_token_sync", refresh)

    with pytest.raises(RuntimeError, match="Not logged in"):
        client_mod.BifrostClient.get_instance()

    refresh.assert_called_once_with("https://api.example.test", "expired-access")
