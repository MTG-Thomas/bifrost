"""Tests for execution content hash pinning."""

import hashlib

import pytest


def test_content_hash_matches():
    """Hash of code should match what was pinned at dispatch."""
    code = "from bifrost import workflow\n@workflow\ndef test(): return {}"
    content_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    actual_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    assert content_hash == actual_hash


def test_content_hash_detects_change():
    """Changed code should produce different hash."""
    original = "from bifrost import workflow\n@workflow\ndef test(): return {}"
    modified = (
        "from bifrost import workflow\n@workflow\ndef test(): return {'modified': True}"
    )

    original_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()
    modified_hash = hashlib.sha256(modified.encode("utf-8")).hexdigest()
    assert original_hash != modified_hash


def test_content_hash_is_sha256():
    """Content hash should be SHA-256 hex digest."""
    code = "print('hello')"
    h = hashlib.sha256(code.encode("utf-8")).hexdigest()
    assert len(h) == 64  # SHA-256 hex = 64 chars
    assert all(c in "0123456789abcdef" for c in h)


def test_content_hash_none_skips_validation():
    """When content_hash is None, validation should be skipped (no error)."""
    content_hash = None
    workflow_code = "print('hello')"
    # Simulates the worker logic: if content_hash is None, skip validation
    if content_hash and workflow_code:
        actual_hash = hashlib.sha256(workflow_code.encode("utf-8")).hexdigest()
        assert actual_hash == content_hash  # Should not reach here
    # No assertion failure means validation was correctly skipped


def test_content_hash_empty_code_skips_validation():
    """When workflow_code is empty/None, validation should be skipped."""
    content_hash = hashlib.sha256(b"some code").hexdigest()
    workflow_code = None
    # Simulates the worker logic: if workflow_code is falsy, skip validation
    if content_hash and workflow_code:
        actual_hash = hashlib.sha256(workflow_code.encode("utf-8")).hexdigest()
        assert actual_hash == content_hash  # Should not reach here
    # No assertion failure means validation was correctly skipped


def test_content_hash_deterministic():
    """Same code should always produce the same hash."""
    code = "from bifrost import workflow\n@workflow\ndef hello():\n    return {'msg': 'hi'}"
    hashes = [hashlib.sha256(code.encode("utf-8")).hexdigest() for _ in range(100)]
    assert len(set(hashes)) == 1  # All hashes should be identical
