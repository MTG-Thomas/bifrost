"""
Unit tests for bifrost.client module injection support.

Tests the client injection pattern used for platform mode workflow execution.
"""

import os
from unittest.mock import patch

import httpx
import pytest


class TestClientInjection:
    """Test client injection for platform mode."""

    @pytest.fixture(autouse=True)
    def setup_client_module(self, tmp_path):
        """Set up client module with mocked credentials for each test."""
        # Patch credentials before importing client module
        with patch("bifrost.client.get_credentials", return_value=None):
            with patch("bifrost.client.is_token_expired", return_value=False):
                # Import client module functions
                from bifrost.client import (
                    BifrostClient,
                    _clear_client,
                    _set_client,
                    get_client,
                )

                self.BifrostClient = BifrostClient
                self._set_client = _set_client
                self._clear_client = _clear_client
                self.get_client = get_client

                # Clear any leftover injected client
                _clear_client()

                yield

                # Clean up
                _clear_client()

    def test_set_and_get_injected_client(self):
        """Test that injected client is returned by get_client()."""
        # Create a test client
        test_client = self.BifrostClient(
            api_url="http://test:8000", access_token="test_token_12345"
        )

        try:
            # Inject the client
            self._set_client(test_client)

            # get_client() should return the injected client
            result = self.get_client()
            assert result is test_client
            assert result.api_url == "http://test:8000"
            assert result._access_token == "test_token_12345"
        finally:
            # Clean up
            self._clear_client()

    def test_clear_client(self):
        """Test that _clear_client() removes the injected client."""
        # Create and inject a client
        test_client = self.BifrostClient(
            api_url="http://test:8000", access_token="test_token_12345"
        )
        self._set_client(test_client)

        # Verify it was set
        assert self.get_client() is test_client

        # Clear the client
        self._clear_client()

        # get_client() should now fall back to credentials file
        # (which won't exist in tests, so it should raise RuntimeError)
        with pytest.raises(RuntimeError, match="Not logged in"):
            self.get_client()

    def test_injected_client_takes_precedence(self):
        """Test that injected client takes precedence over credentials file."""
        # Create and inject a client
        test_client = self.BifrostClient(
            api_url="http://injected:8000", access_token="injected_token"
        )

        try:
            self._set_client(test_client)

            # Even if credentials exist, injected client should be returned
            result = self.get_client()
            assert result is test_client
            assert result.api_url == "http://injected:8000"
        finally:
            self._clear_client()

    def test_no_credentials_raises_error(self):
        """Test that get_client() raises error when no injection and no credentials."""
        # Make sure no client is injected
        self._clear_client()

        # Should raise RuntimeError about not being logged in
        with pytest.raises(RuntimeError, match="Not logged in"):
            self.get_client()

    def test_bifrost_client_initialization(self):
        """Test BifrostClient constructor properly sets up HTTP clients."""
        client = self.BifrostClient(
            api_url="http://example.com:8000/", access_token="token_abc123"
        )

        try:
            # URL should be stripped of trailing slash
            assert client.api_url == "http://example.com:8000"

            # Access token should be stored
            assert client._access_token == "token_abc123"

            # Sync HTTP client should be initialized eagerly
            assert client._sync_http is not None
            assert client._sync_http.headers["Authorization"] == "Bearer token_abc123"
            assert getattr(client._sync_http, "_trust_env") is False

            # Async HTTP client is now lazily initialized per event loop
            # Call _get_async_client() to create it
            http = client._get_async_client()
            assert http is not None
            assert http.headers["Authorization"] == "Bearer token_abc123"
            assert getattr(http, "_trust_env") is False
        finally:
            # Clean up async client (don't use asyncio.run to avoid nested event loop)
            pass

    @pytest.mark.asyncio
    async def test_http_methods_exist(self):
        """Test that all required HTTP methods are available."""
        client = self.BifrostClient(
            api_url="http://test:8000", access_token="test_token"
        )

        try:
            # Verify all required async methods exist
            assert hasattr(client, "get")
            assert hasattr(client, "post")
            assert hasattr(client, "put")
            assert hasattr(client, "patch")
            assert hasattr(client, "delete")
            assert hasattr(client, "stream")
            assert hasattr(client, "close")

            # Verify they're async
            assert callable(client.get)
            assert callable(client.post)
            assert callable(client.put)
            assert callable(client.patch)
            assert callable(client.delete)
            assert callable(client.close)
        finally:
            await client.close()


class TestEnvCredentialRefresh:
    @pytest.mark.asyncio
    async def test_401_refresh_persists_env_sourced_credentials(
        self, monkeypatch, tmp_path
    ):
        """Env-backed sessions do not rewrite a matching CWD .env file."""
        from bifrost import client as client_mod

        dotenv_contents = "\n".join(
            [
                "BIFROST_API_URL=http://localhost:38421",
                "BIFROST_ACCESS_TOKEN=dotenv_access",
                "BIFROST_REFRESH_TOKEN=dotenv_refresh",
                "",
            ]
        )
        dotenv_path = tmp_path / ".env"
        dotenv_path.write_text(dotenv_contents)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("BIFROST_API_URL", "http://localhost:38421")
        monkeypatch.setenv("BIFROST_ACCESS_TOKEN", "old_access")
        monkeypatch.setenv("BIFROST_REFRESH_TOKEN", "old_refresh")

        saved = []

        class StubResponse:
            status_code = 200

            def json(self):
                return {
                    "access_token": "new_access",
                    "refresh_token": "new_refresh",
                    "expires_in": 1800,
                }

        class StubAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, path, json=None):
                assert path == "/auth/refresh"
                assert json == {"refresh_token": "old_refresh"}
                return StubResponse()

        monkeypatch.setattr(client_mod.httpx, "AsyncClient", StubAsyncClient)
        monkeypatch.setattr(
            client_mod, "save_credentials", lambda **kwargs: saved.append(kwargs)
        )

        client = client_mod.BifrostClient("http://localhost:38421", "old_access")
        assert await client._refresh_and_update()
        assert saved == []
        assert client._access_token == "new_access"
        assert client._sync_http.headers["Authorization"] == "Bearer new_access"
        assert os.environ["BIFROST_ACCESS_TOKEN"] == "new_access"
        assert os.environ["BIFROST_REFRESH_TOKEN"] == "new_refresh"
        assert dotenv_path.read_text() == dotenv_contents


@pytest.mark.asyncio
async def test_async_5xx_retry_only_retries_idempotent_methods(monkeypatch):
    from bifrost import client as client_mod

    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(client_mod.asyncio, "sleep", sleep)

    get_responses = iter([
        httpx.Response(503),
        httpx.Response(502),
        httpx.Response(200),
    ])
    get_calls = 0

    async def send_get():
        nonlocal get_calls
        get_calls += 1
        return next(get_responses)

    response = await client_mod._send_with_5xx_retry("GET", send_get)
    assert response.status_code == 200
    assert get_calls == 3
    assert sleeps == [0.5, 1.5]

    post_calls = 0

    async def send_post():
        nonlocal post_calls
        post_calls += 1
        return httpx.Response(503)

    response = await client_mod._send_with_5xx_retry("POST", send_post)
    assert response.status_code == 503
    assert post_calls == 1


def test_sync_5xx_retry_and_error_detail(monkeypatch):
    from bifrost import client as client_mod

    sleeps = []
    monkeypatch.setattr(client_mod.time, "sleep", lambda delay: sleeps.append(delay))

    responses = iter([httpx.Response(504), httpx.Response(200)])
    response = client_mod._send_sync_with_5xx_retry("DELETE", lambda: next(responses))

    assert response.status_code == 200
    assert sleeps == [0.5]

    request = httpx.Request("GET", "https://api.example.test/fail")
    detailed = httpx.Response(
        400,
        json={"detail": "bad input"},
        request=request,
    )
    with pytest.raises(httpx.HTTPStatusError, match="400 Bad Request: bad input"):
        client_mod.raise_for_status_with_detail(detailed)

    plain = httpx.Response(404, text="missing", request=request)
    with pytest.raises(httpx.HTTPStatusError, match="404 Not Found"):
        client_mod.raise_for_status_with_detail(plain)


@pytest.mark.asyncio
async def test_client_context_caches_and_properties(monkeypatch):
    from bifrost import client as client_mod

    calls = []

    class SyncHTTP:
        headers = {"Authorization": "Bearer token"}

        def get(self, path):
            calls.append(path)
            return httpx.Response(
                200,
                json={
                    "user": {"email": "dev@example.test"},
                    "organization": {"id": "org-1"},
                    "default_parameters": {"ticket_id": 123},
                },
                request=httpx.Request("GET", f"https://api.example.test{path}"),
            )

        def close(self):
            pass

    monkeypatch.setattr(client_mod.httpx, "Client", lambda **kwargs: SyncHTTP())

    client = client_mod.BifrostClient("https://api.example.test/", "token")

    assert client.context["user"]["email"] == "dev@example.test"
    assert client.user == {"email": "dev@example.test"}
    assert client.organization == {"id": "org-1"}
    assert client.default_parameters == {"ticket_id": 123}
    assert client.context["organization"]["id"] == "org-1"
    assert calls == ["/api/sdk/context"]


@pytest.mark.asyncio
async def test_client_refreshes_once_after_401(monkeypatch):
    from bifrost import client as client_mod

    class AsyncHTTP:
        def __init__(self, statuses):
            self.statuses = list(statuses)
            self.calls = []

        async def get(self, path, **kwargs):
            self.calls.append((path, kwargs))
            return httpx.Response(self.statuses.pop(0))

        async def aclose(self):
            pass

    clients = [AsyncHTTP([401]), AsyncHTTP([200])]

    class SyncHTTP:
        headers = {"Authorization": "Bearer old"}

        def close(self):
            pass

    monkeypatch.setattr(client_mod.httpx, "Client", lambda **kwargs: SyncHTTP())
    monkeypatch.setattr(client_mod.httpx, "AsyncClient", lambda **kwargs: clients.pop(0))
    async def refresh_token(api_url, observed_access_token):
        assert api_url == "https://api.example.test"
        assert observed_access_token == "old"
        return "new"

    monkeypatch.setattr(client_mod, "refresh_connection_access_token", refresh_token)
    monkeypatch.setattr(
        client_mod,
        "get_credentials",
        lambda **_kwargs: {
            "api_url": "https://api.example.test",
            "access_token": "new",
            "refresh_token": "refresh",
            "expires_at": "later",
        },
    )

    client = client_mod.BifrostClient("https://api.example.test", "old")
    response = await client.get("/api/example", params={"q": "1"})

    assert response.status_code == 200
    assert client._access_token == "new"
    assert client._sync_http.headers["Authorization"] == "Bearer new"


def test_has_credentials_and_thread_local_instance(monkeypatch):
    from bifrost import client as client_mod

    if hasattr(client_mod._thread_local, "bifrost_client"):
        delattr(client_mod._thread_local, "bifrost_client")

    creds = {
        "api_url": "https://api.example.test",
        "access_token": "access",
        "refresh_token": "refresh",
        "expires_at": "later",
    }

    class SyncHTTP:
        headers = {"Authorization": "Bearer access"}

        def close(self):
            pass

    monkeypatch.setattr(client_mod.httpx, "Client", lambda **kwargs: SyncHTTP())
    monkeypatch.setattr(client_mod, "get_credentials", lambda **_kwargs: creds)
    monkeypatch.setattr(client_mod, "is_token_expired", lambda: False)

    assert client_mod.has_credentials() is True
    first = client_mod.BifrostClient.get_instance()
    second = client_mod.BifrostClient.get_instance()

    assert first is second
    assert first.api_url == "https://api.example.test"


async def _async_return(value):
    return value
