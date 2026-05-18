"""Performance benchmarks for Bifrost core modules.

These benchmarks target pure, CPU-bound functions that sit on hot paths
(cache key generation, secret naming, log sanitization, secret redaction).
They have zero external dependencies and are safe to run in any environment.
"""

from __future__ import annotations

from src.core.cache.keys import (
    config_hash_key,
    config_key,
    execution_pending_key,
    form_key,
    forms_hash_key,
    org_key,
    rate_limit_key,
    refresh_token_jti_key,
    role_key,
    roles_hash_key,
    user_forms_key,
)
from src.core.log_safety import log_safe
from src.core.secret_naming import (
    generate_secret_name,
    is_secret_reference,
    sanitize_name_component,
    sanitize_scope,
)
from src.core.secret_string import SecretString, redact_secrets

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------
_ORG_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_USER_UUID = "11111111-2222-3333-4444-555555555555"
_JTI = "jti-abcdef1234567890"
_EXEC_ID = "exec-00000000-1111-2222-3333-444444444444"

_SECRET_REF_NEW = "bifrost-global-api-key-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_SECRET_REF_LEGACY = "org-123--my-secret"
_NOT_A_REF = "just-some-password-123"

_SECRETS = {"super-secret-value", "another-api-key-12345"}


# ---------------------------------------------------------------------------
# Cache key generation — high-frequency operations on every SDK/API call
# ---------------------------------------------------------------------------


def test_bench_config_hash_key_org(benchmark):
    benchmark(config_hash_key, _ORG_UUID)


def test_bench_config_hash_key_global(benchmark):
    benchmark(config_hash_key, None)


def test_bench_config_key(benchmark):
    benchmark(config_key, _ORG_UUID, "smtp_password")


def test_bench_forms_hash_key(benchmark):
    benchmark(forms_hash_key, _ORG_UUID)


def test_bench_form_key(benchmark):
    benchmark(form_key, _ORG_UUID, "form-001")


def test_bench_user_forms_key(benchmark):
    benchmark(user_forms_key, _ORG_UUID, _USER_UUID)


def test_bench_roles_hash_key(benchmark):
    benchmark(roles_hash_key, _ORG_UUID)


def test_bench_role_key(benchmark):
    benchmark(role_key, _ORG_UUID, "admin")


def test_bench_org_key(benchmark):
    benchmark(org_key, _ORG_UUID)


def test_bench_execution_pending_key(benchmark):
    benchmark(execution_pending_key, _EXEC_ID)


def test_bench_rate_limit_key(benchmark):
    benchmark(rate_limit_key, "/api/v1/login", "192.168.1.1")


def test_bench_refresh_token_jti_key(benchmark):
    benchmark(refresh_token_jti_key, _USER_UUID, _JTI)


# ---------------------------------------------------------------------------
# Secret naming — regex sanitization + UUID generation
# ---------------------------------------------------------------------------


def test_bench_sanitize_scope_simple(benchmark):
    benchmark(sanitize_scope, "GLOBAL")


def test_bench_sanitize_scope_complex(benchmark):
    benchmark(sanitize_scope, "org@123!special$$chars")


def test_bench_sanitize_name_component(benchmark):
    benchmark(sanitize_name_component, "my.config.key_value")


def test_bench_generate_secret_name(benchmark):
    benchmark(generate_secret_name, "acme-corp", "smtp_password")


def test_bench_is_secret_reference_new_format(benchmark):
    benchmark(is_secret_reference, _SECRET_REF_NEW)


def test_bench_is_secret_reference_legacy_format(benchmark):
    benchmark(is_secret_reference, _SECRET_REF_LEGACY)


def test_bench_is_secret_reference_not_a_ref(benchmark):
    benchmark(is_secret_reference, _NOT_A_REF)


# ---------------------------------------------------------------------------
# Log safety — regex-based sanitization for log injection prevention
# ---------------------------------------------------------------------------


def test_bench_log_safe_clean(benchmark):
    benchmark(log_safe, "normal log message without special chars")


def test_bench_log_safe_injection(benchmark):
    payload = "line1\nline2\rline3\x1b[31mred\x1b[0m"
    benchmark(log_safe, payload)


def test_bench_log_safe_long_string(benchmark):
    payload = "x" * 500
    benchmark(log_safe, payload)


# ---------------------------------------------------------------------------
# Secret string redaction — recursive deep-walk for sensitive data masking
# ---------------------------------------------------------------------------


def test_bench_secret_string_repr(benchmark):
    s = SecretString("my-secret-password")
    benchmark(repr, s)


def test_bench_secret_string_str(benchmark):
    s = SecretString("my-secret-password")
    benchmark(str, s)


def test_bench_redact_secrets_flat_string(benchmark):
    benchmark(redact_secrets, "the password is super-secret-value here", _SECRETS)


def test_bench_redact_secrets_nested_dict(benchmark):
    obj = {
        "config": {
            "api_key": "another-api-key-12345",
            "host": "example.com",
            "nested": {
                "token": "super-secret-value",
                "port": 443,
            },
        },
        "items": ["safe", "another-api-key-12345", "also-safe"],
    }
    benchmark(redact_secrets, obj, _SECRETS)


def test_bench_redact_secrets_no_matches(benchmark):
    obj = {"key": "value", "num": 42, "list": [1, 2, 3]}
    benchmark(redact_secrets, obj, _SECRETS)
