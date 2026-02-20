from unittest.mock import patch
from src.sdk.context import ExecutionContext


class TestCollectSecretValues:
    """Test secret collection from config dict."""

    def test_collects_secret_type_values(self):
        ctx = ExecutionContext(
            user_id="u1", email="e@e.com", name="Test",
            scope="GLOBAL", organization=None,
            is_platform_admin=False, is_function_key=False,
            execution_id="exec-1",
            _config={
                "api_key": {"value": "encrypted_secret", "type": "secret"},
                "name": {"value": "plain", "type": "string"},
                "count": {"value": "42", "type": "int"},
            },
        )

        with patch("src.core.security.decrypt_secret", return_value="real-secret-value"):
            secrets = ctx._collect_secret_values()

        assert secrets == {"real-secret-value"}

    def test_skips_short_secrets(self):
        ctx = ExecutionContext(
            user_id="u1", email="e@e.com", name="Test",
            scope="GLOBAL", organization=None,
            is_platform_admin=False, is_function_key=False,
            execution_id="exec-1",
            _config={
                "short": {"value": "encrypted_ab", "type": "secret"},
            },
        )

        with patch("src.core.security.decrypt_secret", return_value="ab"):
            secrets = ctx._collect_secret_values()

        assert secrets == set()  # "ab" is too short

    def test_empty_config(self):
        ctx = ExecutionContext(
            user_id="u1", email="e@e.com", name="Test",
            scope="GLOBAL", organization=None,
            is_platform_admin=False, is_function_key=False,
            execution_id="exec-1",
            _config={},
        )

        secrets = ctx._collect_secret_values()
        assert secrets == set()


class TestRegisterDynamicSecret:
    """Test dynamic secret registration on ExecutionContext."""

    def _make_ctx(self, **kwargs):
        return ExecutionContext(
            user_id="u1", email="e@e.com", name="Test",
            scope="GLOBAL", organization=None,
            is_platform_admin=False, is_function_key=False,
            execution_id="exec-1",
            **kwargs,
        )

    def test_register_and_collect(self):
        ctx = self._make_ctx()
        ctx._register_dynamic_secret("super-secret-token")
        assert "super-secret-token" in ctx._collect_secret_values()

    def test_short_secret_excluded(self):
        ctx = self._make_ctx()
        ctx._register_dynamic_secret("ab")
        assert "ab" not in ctx._collect_secret_values()

    def test_none_is_noop(self):
        ctx = self._make_ctx()
        ctx._register_dynamic_secret(None)
        assert ctx._collect_secret_values() == set()

    def test_empty_string_excluded(self):
        ctx = self._make_ctx()
        ctx._register_dynamic_secret("")
        assert ctx._collect_secret_values() == set()

    def test_multiple_secrets(self):
        ctx = self._make_ctx()
        ctx._register_dynamic_secret("first-token-abc")
        ctx._register_dynamic_secret("second-token-xyz")
        secrets = ctx._collect_secret_values()
        assert "first-token-abc" in secrets
        assert "second-token-xyz" in secrets

    def test_merged_with_config_secrets(self):
        ctx = self._make_ctx(
            _config={"api_key": {"value": "encrypted_secret", "type": "secret"}},
        )
        ctx._register_dynamic_secret("oauth-access-token-xyz")
        with patch("src.core.security.decrypt_secret", return_value="real-secret-value"):
            secrets = ctx._collect_secret_values()
        assert "real-secret-value" in secrets
        assert "oauth-access-token-xyz" in secrets
