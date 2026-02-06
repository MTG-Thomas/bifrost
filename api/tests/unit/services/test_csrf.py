"""Tests for CSRF middleware constants defined in src.core.csrf."""

from src.core.csrf import CSRF_EXEMPT_PATHS, CSRF_EXEMPT_PREFIXES, UNSAFE_METHODS


class TestUnsafeMethods:
    """Tests for the UNSAFE_METHODS constant."""

    def test_contains_post(self):
        assert "POST" in UNSAFE_METHODS

    def test_contains_put(self):
        assert "PUT" in UNSAFE_METHODS

    def test_contains_delete(self):
        assert "DELETE" in UNSAFE_METHODS

    def test_contains_patch(self):
        assert "PATCH" in UNSAFE_METHODS

    def test_exactly_four_methods(self):
        assert len(UNSAFE_METHODS) == 4

    def test_exact_set(self):
        assert UNSAFE_METHODS == {"POST", "PUT", "DELETE", "PATCH"}

    def test_get_not_unsafe(self):
        assert "GET" not in UNSAFE_METHODS

    def test_head_not_unsafe(self):
        assert "HEAD" not in UNSAFE_METHODS

    def test_options_not_unsafe(self):
        assert "OPTIONS" not in UNSAFE_METHODS


class TestCsrfExemptPaths:
    """Tests for the CSRF_EXEMPT_PATHS constant."""

    def test_auth_login_exempt(self):
        assert "/auth/login" in CSRF_EXEMPT_PATHS

    def test_auth_register_exempt(self):
        assert "/auth/register" in CSRF_EXEMPT_PATHS

    def test_auth_refresh_exempt(self):
        assert "/auth/refresh" in CSRF_EXEMPT_PATHS

    def test_auth_oauth_callback_exempt(self):
        assert "/auth/oauth/callback" in CSRF_EXEMPT_PATHS

    def test_auth_mfa_login_exempt(self):
        assert "/auth/mfa/login" in CSRF_EXEMPT_PATHS

    def test_auth_mfa_login_setup_exempt(self):
        assert "/auth/mfa/login/setup" in CSRF_EXEMPT_PATHS

    def test_auth_mfa_login_verify_exempt(self):
        assert "/auth/mfa/login/verify" in CSRF_EXEMPT_PATHS

    def test_passkeys_authenticate_options_exempt(self):
        assert "/auth/passkeys/authenticate/options" in CSRF_EXEMPT_PATHS

    def test_passkeys_authenticate_verify_exempt(self):
        assert "/auth/passkeys/authenticate/verify" in CSRF_EXEMPT_PATHS

    def test_setup_passkey_options_exempt(self):
        assert "/auth/setup/passkey/options" in CSRF_EXEMPT_PATHS

    def test_setup_passkey_verify_exempt(self):
        assert "/auth/setup/passkey/verify" in CSRF_EXEMPT_PATHS

    def test_device_code_exempt(self):
        assert "/auth/device/code" in CSRF_EXEMPT_PATHS

    def test_device_token_exempt(self):
        assert "/auth/device/token" in CSRF_EXEMPT_PATHS

    def test_health_exempt(self):
        assert "/health" in CSRF_EXEMPT_PATHS

    def test_ready_exempt(self):
        assert "/ready" in CSRF_EXEMPT_PATHS

    def test_root_exempt(self):
        assert "/" in CSRF_EXEMPT_PATHS

    def test_authorize_exempt(self):
        assert "/authorize" in CSRF_EXEMPT_PATHS

    def test_token_exempt(self):
        assert "/token" in CSRF_EXEMPT_PATHS

    def test_register_exempt(self):
        assert "/register" in CSRF_EXEMPT_PATHS

    def test_mcp_callback_exempt(self):
        assert "/mcp/callback" in CSRF_EXEMPT_PATHS

    def test_is_a_set(self):
        assert isinstance(CSRF_EXEMPT_PATHS, set)


class TestCsrfExemptPrefixes:
    """Tests for the CSRF_EXEMPT_PREFIXES constant."""

    def test_hooks_prefix_present(self):
        assert "/api/hooks/" in CSRF_EXEMPT_PREFIXES

    def test_is_a_tuple(self):
        assert isinstance(CSRF_EXEMPT_PREFIXES, tuple)

    def test_prefix_usable_with_startswith(self):
        """Verify the tuple works with str.startswith() as the middleware uses it."""
        assert "/api/hooks/github".startswith(CSRF_EXEMPT_PREFIXES)
        assert "/api/hooks/stripe/webhook".startswith(CSRF_EXEMPT_PREFIXES)
        assert not "/api/workflows/".startswith(CSRF_EXEMPT_PREFIXES)
