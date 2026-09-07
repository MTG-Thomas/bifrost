"""Request admission must fail closed without replaying vendor operations."""
import importlib
from unittest.mock import AsyncMock

import httpx
import pytest

from src.services.integration_request_slots import validate_limit

sdk = importlib.import_module("bifrost.integrations")


@pytest.mark.parametrize("value", [0, 101, True, "2", 2.5])
def test_invalid_policy_rejected(value):
    with pytest.raises(ValueError):
        validate_limit(value)


def test_unconfigured_policy_is_disabled():
    assert validate_limit(None) is None
    assert validate_limit(2) == 2


def response(**overrides):
    body = dict(enabled=True, acquired=True, lease_remaining_seconds=60, max_request_seconds=30)
    body.update(overrides)
    return httpx.Response(200, json=body, request=httpx.Request("POST", "https://example.test/slots"))


@pytest.fixture
def client(monkeypatch):
    client = AsyncMock()
    client.post.return_value = response()
    monkeypatch.setattr(sdk, "get_client", lambda: client)
    monkeypatch.setattr(sdk, "resolve_scope", lambda scope: scope)
    return client


@pytest.mark.asyncio
async def test_success_releases_exact_owner_token(client):
    operations = []
    async with sdk.integrations.request_slot("Example", scope="global"):
        operations.append("sent")
    assert operations == ["sent"]
    assert client.post.await_count == 2
    acquire, release = client.post.await_args_list
    assert acquire.args[0].endswith("/acquire")
    assert release.args[0].endswith("/release")
    assert acquire.kwargs["json"] == release.kwargs["json"]


@pytest.mark.asyncio
async def test_transport_error_retains_lease_and_does_not_retry(client):
    with pytest.raises(RuntimeError, match="uncertain"):
        async with sdk.integrations.request_slot("Example"):
            raise RuntimeError("uncertain")
    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_release_failure_does_not_turn_success_into_replay_candidate(client):
    client.post.side_effect = [response(), RuntimeError("Redis unavailable")]
    async with sdk.integrations.request_slot("Example"):
        pass
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_admission_failure_never_enters_vendor_body(client):
    client.post.side_effect = RuntimeError("unavailable")
    entered = False
    with pytest.raises(RuntimeError, match="unavailable"):
        async with sdk.integrations.request_slot("Example"):
            entered = True
    assert not entered


@pytest.mark.asyncio
async def test_full_pool_wait_is_bounded(client):
    client.post.return_value = response(acquired=False, lease_remaining_seconds=0)
    with pytest.raises(TimeoutError):
        async with sdk.integrations.request_slot("Example", wait_timeout=0.01):
            pytest.fail("must not enter")
    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_request_wall_clock_timeout_retains_lease(client):
    import asyncio
    with pytest.raises(TimeoutError):
        async with sdk.integrations.request_slot("Example", request_timeout=0.01):
            await asyncio.sleep(1)
    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_elapsed_lease_does_not_start_request(client):
    client.post.return_value = response(lease_remaining_seconds=1)
    with pytest.raises(TimeoutError, match="expired"):
        async with sdk.integrations.request_slot("Example"):
            pytest.fail("must not enter")


@pytest.mark.asyncio
async def test_unconfigured_integration_does_not_release(client):
    client.post.return_value = response(enabled=False, lease_remaining_seconds=0)
    async with sdk.integrations.request_slot("Example"):
        pass
    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_reserves_auth_refresh_bound_for_every_admission_post(client):
    reserved = []
    async with sdk.integrations.request_slot("Example", reserve_http_calls=reserved.append):
        pass
    assert reserved == [3, 3]
    assert all(call.kwargs["retry_safe"] is False for call in client.post.await_args_list)


@pytest.mark.asyncio
async def test_exhausted_admission_budget_prevents_http_call(client):
    def reserve(_calls):
        raise RuntimeError("budget exhausted")
    with pytest.raises(RuntimeError, match="budget exhausted"):
        async with sdk.integrations.request_slot("Example", reserve_http_calls=reserve):
            pytest.fail("must not enter")
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_budget_exhaustion_preserves_success(client):
    remaining = [3]
    def reserve(calls):
        if calls > remaining[0]:
            raise RuntimeError("budget exhausted")
        remaining[0] -= calls
    async with sdk.integrations.request_slot("Example", reserve_http_calls=reserve):
        pass
    assert client.post.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_safe,attempts", [(False, 1), (True, 6)])
async def test_sdk_post_transport_and_refresh_bound(monkeypatch, retry_safe, attempts):
    from bifrost.client import BifrostClient
    client_module = importlib.import_module("bifrost.client")
    transport = AsyncMock()
    transport.post.side_effect = [
        httpx.Response(code, request=httpx.Request("POST", "https://example.test/slots"))
        for _ in range(attempts) for code in (401, 503)
    ]
    concrete = object.__new__(BifrostClient)
    concrete._access_token = "test-token"
    concrete._get_async_client = lambda: transport
    concrete._refresh_and_update = AsyncMock(return_value=True)
    monkeypatch.setattr(client_module.asyncio, "sleep", AsyncMock())
    result = await concrete.post("/api/sdk/integrations/request-slot/acquire", retry_safe=retry_safe)
    assert result.status_code == 503
    assert transport.post.await_count == 2 * attempts
    assert concrete._refresh_and_update.await_count == attempts
    # Each refresh coordinator action issues at most one HTTP request.
    assert transport.post.await_count + concrete._refresh_and_update.await_count == 3 * attempts
