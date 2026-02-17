"""Unit tests for HMAC embed verification."""

import hashlib
import hmac as hmac_module

from src.services.embed_auth import compute_embed_hmac, verify_embed_hmac


class TestComputeEmbedHmac:
    def test_single_param(self):
        result = compute_embed_hmac({"agent_id": "42"}, "my-secret")
        expected = hmac_module.new(
            b"my-secret", b"agent_id=42", hashlib.sha256
        ).hexdigest()
        assert result == expected

    def test_multiple_params_sorted(self):
        """Params should be sorted alphabetically by key."""
        result = compute_embed_hmac(
            {"ticket_id": "1001", "agent_id": "42"}, "my-secret"
        )
        expected = hmac_module.new(
            b"my-secret", b"agent_id=42&ticket_id=1001", hashlib.sha256
        ).hexdigest()
        assert result == expected

    def test_empty_params(self):
        result = compute_embed_hmac({}, "my-secret")
        expected = hmac_module.new(
            b"my-secret", b"", hashlib.sha256
        ).hexdigest()
        assert result == expected


class TestVerifyEmbedHmac:
    def test_valid_hmac(self):
        secret = "test-secret"
        params = {"agent_id": "42", "ticket_id": "1001"}
        valid_hmac = compute_embed_hmac(params, secret)
        params_with_hmac = {**params, "hmac": valid_hmac}
        assert verify_embed_hmac(params_with_hmac, secret) is True

    def test_invalid_hmac(self):
        params = {"agent_id": "42", "hmac": "invalid-garbage"}
        assert verify_embed_hmac(params, "test-secret") is False

    def test_tampered_param(self):
        secret = "test-secret"
        valid_hmac = compute_embed_hmac({"agent_id": "42"}, secret)
        tampered = {"agent_id": "99", "hmac": valid_hmac}
        assert verify_embed_hmac(tampered, secret) is False

    def test_missing_hmac_param(self):
        assert verify_embed_hmac({"agent_id": "42"}, "test-secret") is False
