"""
Unit tests for MCP workflow tool name normalization and mapping.

Tests the functions that convert workflow names to MCP-compatible tool names,
handle duplicate detection, and manage tool name <-> workflow ID mappings.
"""

from uuid import UUID, uuid4


class TestNormalizeToolName:
    """Tests for _normalize_tool_name()."""

    def test_basic_lowercase(self):
        """Should convert to lowercase."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("ReviewTickets") == "reviewtickets"
        assert _normalize_tool_name("UPPER_CASE") == "upper_case"

    def test_spaces_to_underscores(self):
        """Should convert spaces to underscores."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("Review Tickets") == "review_tickets"
        assert _normalize_tool_name("get user data") == "get_user_data"

    def test_hyphens_to_underscores(self):
        """Should convert hyphens to underscores."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("get-user-data") == "get_user_data"
        assert _normalize_tool_name("review-support-tickets") == "review_support_tickets"

    def test_removes_special_characters(self):
        """Should remove non-alphanumeric characters except underscores."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("user@email.com") == "useremailcom"
        assert _normalize_tool_name("price$100") == "price100"
        assert _normalize_tool_name("data[0]") == "data0"

    def test_collapses_multiple_underscores(self):
        """Should collapse multiple underscores into one."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("get___data") == "get_data"
        assert _normalize_tool_name("hello _ _ world") == "hello_world"

    def test_strips_leading_trailing_underscores(self):
        """Should remove leading and trailing underscores."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("_private") == "private"
        assert _normalize_tool_name("data_") == "data"
        assert _normalize_tool_name("_both_") == "both"

    def test_handles_empty_string(self):
        """Should handle empty string gracefully."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("") == ""
        assert _normalize_tool_name("   ") == ""

    def test_handles_only_special_chars(self):
        """Should handle strings with only special characters."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("@#$%") == ""
        assert _normalize_tool_name("---") == ""

    def test_preserves_numbers(self):
        """Should preserve numbers in the name."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("process123") == "process123"
        assert _normalize_tool_name("v2_api") == "v2_api"


class TestGenerateShortSuffix:
    """Tests for _generate_short_suffix()."""

    def test_default_length(self):
        """Should generate 3-character suffix by default."""
        from src.services.mcp_server.server import _generate_short_suffix

        suffix = _generate_short_suffix()
        assert len(suffix) == 3

    def test_custom_length(self):
        """Should generate suffix with custom length."""
        from src.services.mcp_server.server import _generate_short_suffix

        suffix = _generate_short_suffix(5)
        assert len(suffix) == 5

        suffix = _generate_short_suffix(1)
        assert len(suffix) == 1

    def test_alphanumeric_only(self):
        """Should only contain lowercase letters and digits."""
        from src.services.mcp_server.server import _generate_short_suffix

        for _ in range(100):  # Test multiple times for randomness
            suffix = _generate_short_suffix()
            assert suffix.isalnum()
            assert suffix.islower() or suffix.isdigit()

    def test_zero_length(self):
        """Should allow zero-length suffixes for callers that disable disambiguation."""
        from src.services.mcp_server.server import _generate_short_suffix

        assert _generate_short_suffix(0) == ""

    def test_randomness(self):
        """Should generate different suffixes each time."""
        from src.services.mcp_server.server import _generate_short_suffix

        suffixes = {_generate_short_suffix() for _ in range(50)}
        # With 36^3 = 46656 possible combinations, 50 samples should be mostly unique
        assert len(suffixes) > 40


class TestToolNameMappings:
    """Tests for get_workflow_id_for_tool() and get_registered_tool_name()."""

    def test_lookup_before_registration(self):
        """Should return None when tool is not registered."""
        from src.services.mcp_server.server import (
            get_registered_tool_name,
            get_workflow_id_for_tool,
        )

        # Before registration, lookups should return None
        assert get_workflow_id_for_tool("nonexistent_tool") is None
        assert get_registered_tool_name("nonexistent-id") is None

    def test_mappings_are_bidirectional(self):
        """Mappings should work in both directions after registration."""
        from src.services.mcp_server import server

        # Simulate registration by directly updating the mappings
        original_name_to_id = server._TOOL_NAME_TO_WORKFLOW_ID.copy()
        original_id_to_name = server._WORKFLOW_ID_TO_TOOL_NAME.copy()

        try:
            server._TOOL_NAME_TO_WORKFLOW_ID["test_workflow"] = "test-uuid-123"
            server._WORKFLOW_ID_TO_TOOL_NAME["test-uuid-123"] = "test_workflow"

            assert server.get_workflow_id_for_tool("test_workflow") == "test-uuid-123"
            assert server.get_registered_tool_name("test-uuid-123") == "test_workflow"
        finally:
            # Restore original state
            server._TOOL_NAME_TO_WORKFLOW_ID.clear()
            server._TOOL_NAME_TO_WORKFLOW_ID.update(original_name_to_id)
            server._WORKFLOW_ID_TO_TOOL_NAME.clear()
            server._WORKFLOW_ID_TO_TOOL_NAME.update(original_id_to_name)


class TestMCPContext:
    """Tests for MCPContext claim normalization."""

    def test_normalizes_string_uuid_claims(self):
        from src.services.mcp_server.server import MCPContext

        user_id = str(uuid4())
        org_id = str(uuid4())

        context = MCPContext(
            user_id=user_id,
            org_id=org_id,
            is_platform_admin=False,
            is_external=True,
            user_email="user@example.com",
            user_name="User",
            enabled_system_tools=["list_workflows"],
            accessible_namespaces=["docs"],
            session=object(),
        )

        assert context.user_id == UUID(user_id)
        assert context.org_id == UUID(org_id)
        assert context.is_external is True
        assert context.enabled_system_tools == ["list_workflows"]
        assert context.accessible_namespaces == ["docs"]
        assert context.session is not None

    def test_preserves_uuid_and_empty_optional_claims(self):
        from src.services.mcp_server.server import MCPContext

        user_id = uuid4()

        context = MCPContext(user_id=user_id, org_id=None)

        assert context.user_id == user_id
        assert context.org_id is None
        assert context.enabled_system_tools == []
        assert context.accessible_namespaces == []


class TestDuplicateDetection:
    """Tests for duplicate workflow name detection logic."""

    def test_normalize_detects_case_duplicates(self):
        """Names differing only by case should normalize to same value."""
        from src.services.mcp_server.server import _normalize_tool_name

        # These should all normalize to the same thing
        name1 = _normalize_tool_name("ReviewTickets")
        name2 = _normalize_tool_name("reviewtickets")
        name3 = _normalize_tool_name("REVIEWTICKETS")

        assert name1 == name2 == name3

    def test_normalize_detects_separator_duplicates(self):
        """Names differing only by separators should normalize to same value."""
        from src.services.mcp_server.server import _normalize_tool_name

        name1 = _normalize_tool_name("get_user_data")
        name2 = _normalize_tool_name("get-user-data")
        name3 = _normalize_tool_name("get user data")

        assert name1 == name2 == name3


class TestEdgeCases:
    """Tests for edge cases in tool name handling."""

    def test_unicode_characters_removed(self):
        """Unicode characters should be removed during normalization."""
        from src.services.mcp_server.server import _normalize_tool_name

        # Emoji and unicode should be stripped
        assert _normalize_tool_name("hello🔥world") == "helloworld"
        assert _normalize_tool_name("café") == "caf"  # é is removed

    def test_very_long_names(self):
        """Should handle very long workflow names."""
        from src.services.mcp_server.server import _normalize_tool_name

        long_name = "this_is_a_very_long_workflow_name_" * 10
        result = _normalize_tool_name(long_name)
        # Should still work, just be long
        assert len(result) > 100
        assert "_" in result

    def test_numeric_only_names(self):
        """Should handle names that are only numbers."""
        from src.services.mcp_server.server import _normalize_tool_name

        assert _normalize_tool_name("12345") == "12345"
        assert _normalize_tool_name("123_456") == "123_456"
