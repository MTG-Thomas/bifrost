"""
Unit tests for bifrost.client module injection support.

Tests the client injection pattern used for platform mode workflow execution.
"""

from unittest.mock import patch

import os
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
            api_url="http://test:8000",
            access_token="test_token_12345"
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
            api_url="http://test:8000",
            access_token="test_token_12345"
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
            api_url="http://injected:8000",
            access_token="injected_token"
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
            api_url="http://example.com:8000/",
            access_token="token_abc123"
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
            api_url="http://test:8000",
            access_token="test_token"
        )

        try:
            # Verify all required async methods exist
            assert hasattr(client, 'get')
            assert hasattr(client, 'post')
            assert hasattr(client, 'put')
            assert hasattr(client, 'patch')
            assert hasattr(client, 'delete')
            assert hasattr(client, 'stream')
            assert hasattr(client, 'close')

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
    async def test_401_refresh_persists_env_sourced_credentials(self, monkeypatch, tmp_path):
        """Env-backed sessions persist rotated tokens to the process environment."""
        from bifrost import client as client_mod

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
        monkeypatch.setattr(client_mod, "save_credentials", lambda **kwargs: saved.append(kwargs))

        client = client_mod.BifrostClient("http://localhost:38421", "old_access")
        assert await client._refresh_and_update()
        assert saved == []
        assert client._access_token == "new_access"
        assert client._sync_http.headers["Authorization"] == "Bearer new_access"
        assert os.environ["BIFROST_ACCESS_TOKEN"] == "new_access"
        assert os.environ["BIFROST_REFRESH_TOKEN"] == "new_refresh"
