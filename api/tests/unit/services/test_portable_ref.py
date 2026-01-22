"""
Unit tests for portable reference translation utilities.

Tests the new portable_ref column integration and direct lookup functions
that replace the O(n) map building approach.
"""

from src.services.file_storage.ref_translation import (
    normalize_portable_ref,
    strip_portable_ref_prefix,
    _looks_like_uuid,
)


class TestNormalizePortableRef:
    """Tests for normalize_portable_ref function."""

    def test_adds_prefix_to_legacy_format(self):
        """Legacy refs without prefix get workflow:: added."""
        ref = "workflows/my_module.py::my_function"
        result = normalize_portable_ref(ref)
        assert result == "workflow::workflows/my_module.py::my_function"

    def test_preserves_already_prefixed_refs(self):
        """Refs with workflow:: prefix are unchanged."""
        ref = "workflow::workflows/my_module.py::my_function"
        result = normalize_portable_ref(ref)
        assert result == "workflow::workflows/my_module.py::my_function"

    def test_handles_empty_string(self):
        """Empty string gets prefix added."""
        result = normalize_portable_ref("")
        assert result == "workflow::"

    def test_handles_complex_paths(self):
        """Complex paths with subdirectories are handled."""
        ref = "workflows/handlers/api/users.py::create_user"
        result = normalize_portable_ref(ref)
        assert result == "workflow::workflows/handlers/api/users.py::create_user"


class TestStripPortableRefPrefix:
    """Tests for strip_portable_ref_prefix function."""

    def test_removes_prefix_from_new_format(self):
        """Removes workflow:: prefix from refs that have it."""
        ref = "workflow::workflows/my_module.py::my_function"
        result = strip_portable_ref_prefix(ref)
        assert result == "workflows/my_module.py::my_function"

    def test_preserves_refs_without_prefix(self):
        """Refs without prefix are unchanged."""
        ref = "workflows/my_module.py::my_function"
        result = strip_portable_ref_prefix(ref)
        assert result == "workflows/my_module.py::my_function"

    def test_handles_empty_string(self):
        """Empty string is unchanged."""
        result = strip_portable_ref_prefix("")
        assert result == ""

    def test_handles_prefix_only(self):
        """String that is just the prefix returns empty."""
        result = strip_portable_ref_prefix("workflow::")
        assert result == ""


class TestLooksLikeUuid:
    """Tests for _looks_like_uuid helper function."""

    def test_recognizes_valid_uuid(self):
        """Valid UUID format is recognized."""
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        assert _looks_like_uuid(uuid) is True

    def test_rejects_too_short(self):
        """Strings too short are rejected."""
        assert _looks_like_uuid("550e8400-e29b-41d4") is False

    def test_rejects_too_long(self):
        """Strings too long are rejected."""
        assert _looks_like_uuid("550e8400-e29b-41d4-a716-446655440000-extra") is False

    def test_rejects_wrong_hyphen_positions(self):
        """UUIDs with hyphens in wrong positions are rejected."""
        # Missing first hyphen
        assert _looks_like_uuid("550e84000e29b-41d4-a716-446655440000") is False
        # Wrong second hyphen position
        assert _looks_like_uuid("550e8400-e29b041d4-a716-446655440000") is False

    def test_rejects_no_hyphens(self):
        """UUID without hyphens is rejected."""
        assert _looks_like_uuid("550e8400e29b41d4a716446655440000") is False

    def test_rejects_portable_ref(self):
        """Portable refs are rejected."""
        ref = "workflows/my_module.py::my_function"
        assert _looks_like_uuid(ref) is False

    def test_rejects_empty_string(self):
        """Empty string is rejected."""
        assert _looks_like_uuid("") is False


class TestPortableRefRoundTrip:
    """Tests for normalize/strip round-trip consistency."""

    def test_strip_then_normalize_returns_original(self):
        """Stripping then normalizing returns the original prefixed ref."""
        original = "workflow::workflows/test.py::my_func"
        stripped = strip_portable_ref_prefix(original)
        normalized = normalize_portable_ref(stripped)
        assert normalized == original

    def test_normalize_then_strip_returns_original(self):
        """Normalizing then stripping returns the original legacy ref."""
        original = "workflows/test.py::my_func"
        normalized = normalize_portable_ref(original)
        stripped = strip_portable_ref_prefix(normalized)
        assert stripped == original
