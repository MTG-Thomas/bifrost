"""
Unit tests for MCP Authentication.

Tests the BifrostAuthProvider class which implements OAuth 2.1 for MCP:
- OAuth discovery metadata endpoints
- Authorization code flow with PKCE
- Token verification for MCP requests

Uses mocked dependencies for fast, isolated testing.
"""

import base64
import hashlib
import json
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from src.core.security import create_access_token
from src.services.mcp_server.auth import (
    BifrostAuthProvider,
    create_bifrost_auth_provider,
    _mcp_auth_code_key,
    _mcp_client_key,
    _mcp_state_key,
)


# ==================== Fixtures ====================


@pytest.fixture
def auth_provider() -> BifrostAuthProvider:
    """Create a BifrostAuthProvider with test base URL."""
    return BifrostAuthProvider(base_url="https://test.example.com")


@pytest.fixture
def admin_token_payload() -> dict:
    """Create a payload for a platform admin user."""
    return {
        "sub": str(uuid4()),
        "email": "admin@platform.local",
        "name": "Platform Admin",
        "is_superuser": True,
        "org_id": str(uuid4()),
    }


@pytest.fixture
def regular_user_payload() -> dict:
    """Create a payload for a regular org user."""
    return {
        "sub": str(uuid4()),
        "email": "user@org.local",
        "name": "Regular User",
        "is_superuser": False,
        "org_id": str(uuid4()),
    }


@pytest.fixture
def admin_access_token(admin_token_payload) -> str:
    """Create a valid access token for a platform admin."""
    return create_access_token(admin_token_payload)


@pytest.fixture
def regular_user_access_token(regular_user_payload) -> str:
    """Create a valid access token for a regular user."""
    return create_access_token(regular_user_payload)


@pytest.fixture
def expired_token(admin_token_payload) -> str:
    """Create an expired access token."""
    return create_access_token(
        admin_token_payload,
        expires_delta=timedelta(seconds=-1),  # Already expired
    )


# ==================== BifrostAuthProvider Tests ====================


class TestBifrostAuthProviderInit:
    """Tests for BifrostAuthProvider initialization."""

    def test_uses_provided_base_url(self):
        """Should use the provided base URL."""
        provider = BifrostAuthProvider(base_url="https://custom.example.com")
        assert provider.base_url == "https://custom.example.com"
        assert provider.issuer == "https://custom.example.com"

    def test_strips_trailing_slash(self):
        """Should strip trailing slash from base URL."""
        provider = BifrostAuthProvider(base_url="https://example.com/")
        assert provider.base_url == "https://example.com"

    @patch("src.config.get_settings")
    def test_falls_back_to_settings(self, mock_get_settings):
        """Should fall back to settings.mcp_base_url if no base_url provided."""
        mock_settings = MagicMock()
        mock_settings.public_url = "https://settings.example.com"
        mock_get_settings.return_value = mock_settings

        provider = BifrostAuthProvider()
        assert provider.base_url == "https://settings.example.com"


class TestGetRoutes:
    """Tests for get_routes() method."""

    def test_returns_oauth_routes(self, auth_provider):
        """Should return all required OAuth routes."""
        routes = auth_provider.get_routes()

        # Get route paths
        paths = [route.path for route in routes]

        # Check all required OAuth endpoints
        assert "/.well-known/oauth-authorization-server" in paths
        assert "/.well-known/oauth-protected-resource" in paths
        assert "/authorize" in paths
        assert "/token" in paths
        assert "/register" in paths
        assert "/mcp/callback" in paths


class TestAuthorizationServerMetadata:
    """Tests for OAuth authorization server metadata endpoint."""

    @pytest.mark.asyncio
    async def test_returns_correct_metadata(self, auth_provider):
        """Should return RFC 8414 compliant metadata."""
        mock_request = MagicMock()

        response = await auth_provider._authorization_server_metadata(mock_request)
        data = response.body.decode()

        import json
        metadata = json.loads(data)

        assert metadata["issuer"] == "https://test.example.com"
        assert metadata["authorization_endpoint"] == "https://test.example.com/authorize"
        assert metadata["token_endpoint"] == "https://test.example.com/token"
        assert metadata["registration_endpoint"] == "https://test.example.com/register"
        assert "code" in metadata["response_types_supported"]
        assert "authorization_code" in metadata["grant_types_supported"]
        assert "S256" in metadata["code_challenge_methods_supported"]


class TestProtectedResourceMetadata:
    """Tests for OAuth protected resource metadata endpoint."""

    @pytest.mark.asyncio
    async def test_returns_correct_metadata(self, auth_provider):
        """Should return RFC 9728 compliant metadata."""
        mock_request = MagicMock()

        response = await auth_provider._protected_resource_metadata(mock_request)
        data = response.body.decode()

        import json
        metadata = json.loads(data)

        assert metadata["resource"] == "https://test.example.com/mcp"
        assert "https://test.example.com" in metadata["authorization_servers"]
        assert "mcp:access" in metadata["scopes_supported"]
        assert "header" in metadata["bearer_methods_supported"]


class TestMcpBoundTokens:
    """MCP OAuth tokens must not be reusable as first-party REST API tokens."""

    @pytest.mark.asyncio
    async def test_mcp_access_token_is_rejected_by_rest_auth(self):
        from src.core.auth import get_current_user_optional

        token = create_access_token({
            "sub": str(uuid4()),
            "email": "user@org.local",
            "name": "Regular User",
            "is_superuser": False,
            "org_id": str(uuid4()),
            "mcp": True,
            "scope": "mcp:access",
        })
        request = MagicMock()
        request.cookies = {}
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=token,
        )

        user = await get_current_user_optional(request, credentials, MagicMock())

        assert user is None


class TestCheckMcpAccess:
    """Tests for MCP access permission checking.

    Access is gated only on the ``enabled`` master switch. Per-user/tool access
    is handled downstream by ``MCPToolAccessService`` via agent role membership,
    so a non-superuser with no matching roles will connect successfully and
    simply see an empty tool set.
    """

    @pytest.mark.asyncio
    async def test_allows_non_admin_when_enabled(self, auth_provider):
        """Non-admins are allowed through — role-scoped tools are filtered later."""
        mock_config = MagicMock()
        mock_config.enabled = True

        with patch("src.core.database.get_db_context") as mock_db, \
             patch("src.services.mcp_server.config_service.get_mcp_config_cached") as mock_get_config:
            mock_db.return_value.__aenter__ = AsyncMock()
            mock_db.return_value.__aexit__ = AsyncMock()
            mock_get_config.return_value = mock_config

            result = await auth_provider._check_mcp_access({"is_superuser": False})
            assert result is True

    @pytest.mark.asyncio
    async def test_allows_admin_when_enabled(self, auth_provider):
        """Platform admins are allowed through."""
        mock_config = MagicMock()
        mock_config.enabled = True

        with patch("src.core.database.get_db_context") as mock_db, \
             patch("src.services.mcp_server.config_service.get_mcp_config_cached") as mock_get_config:
            mock_db.return_value.__aenter__ = AsyncMock()
            mock_db.return_value.__aexit__ = AsyncMock()
            mock_get_config.return_value = mock_config

            result = await auth_provider._check_mcp_access({"is_superuser": True})
            assert result is True

    @pytest.mark.asyncio
    async def test_denies_when_disabled(self, auth_provider):
        """Should deny all access when MCP is disabled, including admins."""
        mock_config = MagicMock()
        mock_config.enabled = False

        with patch("src.core.database.get_db_context") as mock_db, \
             patch("src.services.mcp_server.config_service.get_mcp_config_cached") as mock_get_config:
            mock_db.return_value.__aenter__ = AsyncMock()
            mock_db.return_value.__aexit__ = AsyncMock()
            mock_get_config.return_value = mock_config

            result = await auth_provider._check_mcp_access({"is_superuser": True})
            assert result is False

    @pytest.mark.asyncio
    async def test_allows_when_config_lookup_fails(self, auth_provider):
        """Config lookup errors should fail open to preserve existing behavior."""
        with patch("src.core.database.get_db_context", side_effect=RuntimeError("db down")):
            result = await auth_provider._check_mcp_access({"is_superuser": False})

        assert result is True


class TestVerifyToken:
    """Tests for bearer token verification and auth context construction."""

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_token(self, auth_provider):
        with patch("src.core.security.decode_token", return_value=None), \
             patch.object(auth_provider, "_check_mcp_access", new_callable=AsyncMock) as mock_check:
            result = await auth_provider.verify_token("bad-token")

        assert result is None
        mock_check.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_none_when_mcp_access_denied(self, auth_provider):
        payload = {"sub": "user-1", "email": "user@example.com"}

        with patch("src.core.security.decode_token", return_value=payload), \
             patch.object(auth_provider, "_check_mcp_access", new_callable=AsyncMock, return_value=False), \
             patch.object(auth_provider, "_get_user_roles", new_callable=AsyncMock) as mock_roles:
            result = await auth_provider.verify_token("valid-token")

        assert result is None
        mock_roles.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_builds_access_token_claims_with_roles(self, auth_provider):
        payload = {
            "sub": "user-1",
            "email": "user@example.com",
            "name": "MCP User",
            "is_superuser": False,
            "is_external": True,
            "org_id": "org-1",
            "exp": 123456,
        }

        with patch("src.core.security.decode_token", return_value=payload), \
             patch.object(auth_provider, "_check_mcp_access", new_callable=AsyncMock, return_value=True), \
             patch.object(auth_provider, "_get_user_roles", new_callable=AsyncMock, return_value=["Analyst"]):
            result = await auth_provider.verify_token("valid-token")

        assert result is not None
        assert result.token == "valid-token"
        assert result.client_id == "user-1"
        assert result.scopes == ["mcp:access"]
        assert result.expires_at == 123456
        assert result.claims == {
            "user_id": "user-1",
            "email": "user@example.com",
            "name": "MCP User",
            "is_superuser": False,
            "is_external": True,
            "org_id": "org-1",
            "roles": ["Analyst"],
        }


class TestGetUserRoles:
    """Tests for role lookup error branches."""

    @pytest.mark.asyncio
    async def test_returns_empty_roles_without_user_id(self, auth_provider):
        assert await auth_provider._get_user_roles(None) == []

    @pytest.mark.asyncio
    async def test_returns_empty_roles_when_lookup_fails(self, auth_provider):
        with patch("src.core.database.get_db_context", side_effect=RuntimeError("db unavailable")):
            assert await auth_provider._get_user_roles("user-1") == []


class TestTokenEndpoint:
    """Tests for OAuth token endpoint parsing and error branches."""

    @staticmethod
    def _request_with_form(form_data: dict) -> MagicMock:
        request = MagicMock()
        request.form = AsyncMock(return_value=form_data)
        return request

    @pytest.mark.asyncio
    async def test_rejects_unsupported_grant_type(self, auth_provider):
        response = await auth_provider._token(self._request_with_form({"grant_type": "password"}))

        assert response.status_code == 400
        assert json.loads(response.body)["error"] == "unsupported_grant_type"

    @pytest.mark.asyncio
    async def test_authorization_code_requires_core_parameters(self, auth_provider):
        response = await auth_provider._token(self._request_with_form({
            "grant_type": "authorization_code",
            "code": "code-1",
        }))

        assert response.status_code == 400
        assert json.loads(response.body)["error"] == "invalid_request"

    @pytest.mark.asyncio
    async def test_authorization_code_rejects_missing_or_expired_code(self, auth_provider):
        redis = AsyncMock()
        redis.get.return_value = None

        with patch("src.core.cache.get_shared_redis", AsyncMock(return_value=redis)):
            response = await auth_provider._token(self._request_with_form({
                "grant_type": "authorization_code",
                "code": "missing-code",
                "redirect_uri": "https://client.example/callback",
                "code_verifier": "verifier",
                "client_id": "client-1",
            }))

        assert response.status_code == 400
        assert json.loads(response.body)["error"] == "invalid_grant"
        redis.get.assert_awaited_once_with(_mcp_auth_code_key("missing-code"))

    @pytest.mark.asyncio
    async def test_authorization_code_rejects_redirect_uri_mismatch(self, auth_provider):
        redis = AsyncMock()
        redis.get.return_value = json.dumps({
            "redirect_uri": "https://client.example/callback",
            "client_id": "client-1",
            "code_challenge": "challenge",
        })

        with patch("src.core.cache.get_shared_redis", AsyncMock(return_value=redis)):
            response = await auth_provider._token(self._request_with_form({
                "grant_type": "authorization_code",
                "code": "code-1",
                "redirect_uri": "https://evil.example/callback",
                "code_verifier": "verifier",
                "client_id": "client-1",
            }))

        assert response.status_code == 400
        assert json.loads(response.body)["error_description"] == "redirect_uri mismatch"
        redis.delete.assert_awaited_once_with(_mcp_auth_code_key("code-1"))

    @pytest.mark.asyncio
    async def test_authorization_code_rejects_client_id_mismatch(self, auth_provider):
        redis = AsyncMock()
        redis.get.return_value = json.dumps({
            "redirect_uri": "https://client.example/callback",
            "client_id": "client-1",
            "code_challenge": "challenge",
        })

        with patch("src.core.cache.get_shared_redis", AsyncMock(return_value=redis)):
            response = await auth_provider._token(self._request_with_form({
                "grant_type": "authorization_code",
                "code": "code-1",
                "redirect_uri": "https://client.example/callback",
                "code_verifier": "verifier",
                "client_id": "client-2",
            }))

        assert response.status_code == 400
        assert json.loads(response.body)["error_description"] == "client_id mismatch"

    @pytest.mark.asyncio
    async def test_authorization_code_rejects_invalid_pkce_verifier(self, auth_provider):
        redis = AsyncMock()
        redis.get.return_value = json.dumps({
            "redirect_uri": "https://client.example/callback",
            "client_id": "client-1",
            "code_challenge": "not-the-verifier-hash",
        })

        with patch("src.core.cache.get_shared_redis", AsyncMock(return_value=redis)):
            response = await auth_provider._token(self._request_with_form({
                "grant_type": "authorization_code",
                "code": "code-1",
                "redirect_uri": "https://client.example/callback",
                "code_verifier": "verifier",
                "client_id": "client-1",
            }))

        assert response.status_code == 400
        assert json.loads(response.body)["error_description"] == "Invalid code_verifier"

    @pytest.mark.asyncio
    async def test_authorization_code_returns_tokens_with_external_claim(self, auth_provider):
        verifier = "correct-verifier"
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        redis = AsyncMock()
        redis.get.return_value = json.dumps({
            "user_id": "user-1",
            "email": "user@example.com",
            "redirect_uri": "https://client.example/callback",
            "client_id": "client-1",
            "code_challenge": challenge,
            "scope": "mcp:access custom",
        })
        user = MagicMock()
        user.id = "user-1"
        user.email = "user@example.com"
        user.name = "MCP User"
        user.is_superuser = False
        user.organization_id = "org-1"
        db = MagicMock()
        db_context = MagicMock()
        db_context.__aenter__ = AsyncMock(return_value=db)
        db_context.__aexit__ = AsyncMock(return_value=None)

        with patch("src.core.cache.get_shared_redis", AsyncMock(return_value=redis)), \
             patch("src.core.database.get_db_context", return_value=db_context), \
             patch("src.repositories.users.UserRepository") as mock_repo_cls, \
             patch("src.services.mcp_server.auth.resolve_external_claim", AsyncMock(return_value=True)) as mock_external, \
             patch("src.core.security.create_access_token", return_value="access-token") as mock_access, \
             patch("src.core.security.create_refresh_token", return_value=("refresh-token", "jti")):
            mock_repo_cls.return_value.get_by_id = AsyncMock(return_value=user)

            response = await auth_provider._token(self._request_with_form({
                "grant_type": "authorization_code",
                "code": "code-1",
                "redirect_uri": "https://client.example/callback",
                "code_verifier": verifier,
                "client_id": "client-1",
            }))

        body = json.loads(response.body)
        assert response.status_code == 200
        assert body["access_token"] == "access-token"
        assert body["refresh_token"] == "refresh-token"
        assert body["scope"] == "mcp:access custom"
        mock_external.assert_awaited_once_with(db, user)
        assert mock_access.call_args.kwargs["data"]["is_external"] is True

    @pytest.mark.asyncio
    async def test_refresh_token_requires_refresh_token(self, auth_provider):
        response = await auth_provider._token(self._request_with_form({"grant_type": "refresh_token"}))

        assert response.status_code == 400
        assert json.loads(response.body)["error_description"] == "Missing refresh_token"

    @pytest.mark.asyncio
    async def test_refresh_token_rejects_invalid_payload(self, auth_provider):
        with patch("src.core.security.decode_token", return_value=None):
            response = await auth_provider._token(self._request_with_form({
                "grant_type": "refresh_token",
                "refresh_token": "bad-refresh",
            }))

        assert response.status_code == 400
        assert json.loads(response.body)["error_description"] == "Invalid refresh token"

    @pytest.mark.asyncio
    async def test_refresh_token_rejects_missing_user(self, auth_provider):
        db_context = MagicMock()
        db_context.__aenter__ = AsyncMock(return_value=MagicMock())
        db_context.__aexit__ = AsyncMock(return_value=None)

        with patch("src.core.security.decode_token", return_value={"sub": "user-1"}), \
             patch("src.core.database.get_db_context", return_value=db_context), \
             patch("src.repositories.users.UserRepository") as mock_repo_cls:
            mock_repo_cls.return_value.get_by_id = AsyncMock(return_value=None)

            response = await auth_provider._token(self._request_with_form({
                "grant_type": "refresh_token",
                "refresh_token": "refresh-token",
            }))

        assert response.status_code == 400
        assert json.loads(response.body)["error_description"] == "User not found"

    @pytest.mark.asyncio
    async def test_refresh_token_returns_rotated_tokens(self, auth_provider):
        user = MagicMock()
        user.id = "user-1"
        user.email = "user@example.com"
        user.name = "MCP User"
        user.is_superuser = True
        user.organization_id = None
        db = MagicMock()
        db_context = MagicMock()
        db_context.__aenter__ = AsyncMock(return_value=db)
        db_context.__aexit__ = AsyncMock(return_value=None)

        with patch("src.core.security.decode_token", return_value={"sub": "user-1"}), \
             patch("src.core.database.get_db_context", return_value=db_context), \
             patch("src.repositories.users.UserRepository") as mock_repo_cls, \
             patch("src.services.mcp_server.auth.resolve_external_claim", AsyncMock(return_value=False)), \
             patch("src.core.security.create_access_token", return_value="new-access") as mock_access, \
             patch("src.core.security.create_refresh_token", return_value=("new-refresh", "jti")):
            mock_repo_cls.return_value.get_by_id = AsyncMock(return_value=user)

            response = await auth_provider._token(self._request_with_form({
                "grant_type": "refresh_token",
                "refresh_token": "refresh-token",
            }))

        body = json.loads(response.body)
        assert response.status_code == 200
        assert body["access_token"] == "new-access"
        assert body["refresh_token"] == "new-refresh"
        assert body["scope"] == "mcp:access"
        token_data = mock_access.call_args.kwargs["data"]
        assert token_data["is_superuser"] is True
        assert token_data["is_external"] is False
        assert token_data["org_id"] is None


class TestRegisterEndpoint:
    """Tests for dynamic client registration parsing and persistence."""

    @pytest.mark.asyncio
    async def test_rejects_invalid_json_body(self, auth_provider):
        request = MagicMock()
        request.json = AsyncMock(side_effect=ValueError("bad json"))

        response = await auth_provider._register(request)

        assert response.status_code == 400
        assert json.loads(response.body)["error_description"] == "Invalid JSON body"

    @pytest.mark.asyncio
    async def test_registers_client_with_defaults(self, auth_provider):
        redis = AsyncMock()
        request = MagicMock()
        request.json = AsyncMock(return_value={})

        with patch("src.core.cache.get_shared_redis", AsyncMock(return_value=redis)), \
             patch("src.services.mcp_server.auth.secrets.token_urlsafe", return_value="client-1"):
            response = await auth_provider._register(request)

        body = json.loads(response.body)
        assert response.status_code == 201
        assert body["client_id"] == "client-1"
        assert body["client_name"] == "MCP Client"
        assert body["redirect_uris"] == []
        redis.setex.assert_awaited_once()


# ==================== Redis Key Functions Tests ====================


class TestRedisKeys:
    """Tests for Redis key generation functions."""

    def test_mcp_auth_code_key(self):
        """Should generate correct auth code key."""
        key = _mcp_auth_code_key("test-code-123")
        assert key == "bifrost:mcp:auth_code:test-code-123"

    def test_mcp_client_key(self):
        """Should generate correct client key."""
        key = _mcp_client_key("client-id-456")
        assert key == "bifrost:mcp:client:client-id-456"

    def test_mcp_state_key(self):
        """Should generate correct state key."""
        key = _mcp_state_key("state-789")
        assert key == "bifrost:mcp:state:state-789"


# ==================== Factory Function Tests ====================


class TestCreateBifrostAuthProvider:
    """Tests for the create_bifrost_auth_provider factory function."""

    def test_creates_provider_with_base_url(self):
        """Should create provider with specified base URL."""
        provider = create_bifrost_auth_provider("https://factory.example.com")
        assert isinstance(provider, BifrostAuthProvider)
        assert provider.base_url == "https://factory.example.com"

    @patch("src.config.get_settings")
    def test_creates_provider_with_defaults(self, mock_get_settings):
        """Should create provider with default settings when no base_url provided."""
        mock_settings = MagicMock()
        mock_settings.public_url = "https://default.example.com"
        mock_get_settings.return_value = mock_settings

        provider = create_bifrost_auth_provider()
        assert isinstance(provider, BifrostAuthProvider)
        assert provider.base_url == "https://default.example.com"
