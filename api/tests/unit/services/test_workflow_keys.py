"""Tests for workflow key generation utilities."""

import hashlib
import re

from src.services.workflow_keys import generate_workflow_key


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

    def test_hashed_key_is_valid_hex_64_chars(self):
        _, hashed_key = generate_workflow_key()
        assert len(hashed_key) == 64
        assert re.fullmatch(r"[0-9a-f]{64}", hashed_key), (
            f"Hashed key is not valid lowercase hex: {hashed_key}"
        )

    def test_hash_matches_sha256_of_raw_key(self):
        raw_key, hashed_key = generate_workflow_key()
        expected = hashlib.sha256(raw_key.encode()).hexdigest()
        assert hashed_key == expected

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
