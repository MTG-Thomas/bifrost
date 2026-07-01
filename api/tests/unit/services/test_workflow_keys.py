"""Tests for workflow key generation utilities."""

import re

from src.services.workflow_keys import generate_workflow_key, verify_workflow_key


class TestGenerateWorkflowKey:

    def test_returns_tuple_of_two_strings(self):
        raw_key, hashed_key = generate_workflow_key()
        assert isinstance(raw_key, str)
        assert isinstance(hashed_key, str)

    def test_raw_key_is_url_safe(self):
        raw_key, _ = generate_workflow_key()
        # URL-safe base64 uses only alphanumeric, hyphen, and underscore
        assert re.fullmatch(r"[A-Za-z0-9_-]+", raw_key), (
            f"Raw key contains non-URL-safe characters: {raw_key}"
        )

    def test_hashed_key_is_bcrypt_hash(self):
        _, hashed_key = generate_workflow_key()
        assert hashed_key.startswith("$2")

    def test_hash_verifies_raw_key(self):
        raw_key, hashed_key = generate_workflow_key()
        assert verify_workflow_key(raw_key, hashed_key)

    def test_two_calls_return_different_keys(self):
        raw1, hashed1 = generate_workflow_key()
        raw2, hashed2 = generate_workflow_key()
        assert raw1 != raw2
        assert hashed1 != hashed2

    def test_raw_key_length_is_reasonable(self):
        raw_key, _ = generate_workflow_key()
        assert len(raw_key) > 20, (
            f"Raw key is too short ({len(raw_key)} chars): {raw_key}"
        )
