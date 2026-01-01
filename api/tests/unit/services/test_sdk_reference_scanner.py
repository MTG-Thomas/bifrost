"""
Unit tests for SDK Reference Scanner Service.

Tests the regex extraction and validation logic for detecting
missing config and integration references in Python files.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.services.sdk_reference_scanner import (
    SDKReferenceScanner,
    SDKIssue,
    CONFIG_PATTERN,
    INTEGRATIONS_PATTERN,
)


class TestPatternMatching:
    """Test regex pattern matching."""

    def test_config_pattern_basic(self):
        """Test basic config.get pattern."""
        code = '''config.get("api_key")'''
        matches = CONFIG_PATTERN.findall(code)
        assert matches == ["api_key"]

    def test_config_pattern_with_await(self):
        """Test config.get with await."""
        code = '''await config.get("api_key")'''
        matches = CONFIG_PATTERN.findall(code)
        assert matches == ["api_key"]

    def test_config_pattern_single_quotes(self):
        """Test config.get with single quotes."""
        code = '''config.get('api_key')'''
        matches = CONFIG_PATTERN.findall(code)
        assert matches == ["api_key"]

    def test_config_pattern_multiple(self):
        """Test multiple config.get calls."""
        code = '''
        url = await config.get("api_url")
        key = await config.get("api_key")
        timeout = config.get('timeout')
        '''
        matches = CONFIG_PATTERN.findall(code)
        assert set(matches) == {"api_url", "api_key", "timeout"}

    def test_config_pattern_ignores_default_value(self):
        """Test that config.get with default value is ignored."""
        code = '''config.get("optional_key", "default_value")'''
        matches = CONFIG_PATTERN.findall(code)
        assert matches == []

    def test_config_pattern_ignores_default_with_await(self):
        """Test that await config.get with default value is ignored."""
        code = '''await config.get("optional_key", "default")'''
        matches = CONFIG_PATTERN.findall(code)
        assert matches == []

    def test_config_pattern_ignores_default_single_quotes(self):
        """Test that config.get with default value using single quotes is ignored."""
        code = '''config.get('optional_key', 'default')'''
        matches = CONFIG_PATTERN.findall(code)
        assert matches == []

    def test_config_pattern_mixed_with_and_without_defaults(self):
        """Test mix of config.get with and without defaults."""
        code = '''
        required = config.get("required_key")
        optional = config.get("optional_key", "default")
        also_required = await config.get("another_required")
        also_optional = await config.get("another_optional", None)
        '''
        matches = CONFIG_PATTERN.findall(code)
        assert set(matches) == {"required_key", "another_required"}

    def test_integrations_pattern_basic(self):
        """Test basic integrations.get pattern."""
        code = '''integrations.get("HaloPSA")'''
        matches = INTEGRATIONS_PATTERN.findall(code)
        assert matches == ["HaloPSA"]

    def test_integrations_pattern_with_await(self):
        """Test integrations.get with await."""
        code = '''await integrations.get("HaloPSA")'''
        matches = INTEGRATIONS_PATTERN.findall(code)
        assert matches == ["HaloPSA"]

    def test_integrations_pattern_single_quotes(self):
        """Test integrations.get with single quotes."""
        code = '''integrations.get('Microsoft Partner')'''
        matches = INTEGRATIONS_PATTERN.findall(code)
        assert matches == ["Microsoft Partner"]

    def test_integrations_pattern_multiple(self):
        """Test multiple integrations.get calls."""
        code = '''
        halo = await integrations.get("HaloPSA")
        msft = await integrations.get("Microsoft Partner")
        '''
        matches = INTEGRATIONS_PATTERN.findall(code)
        assert set(matches) == {"HaloPSA", "Microsoft Partner"}

    def test_pattern_matches_commented_code(self):
        """Test that raw pattern matches commented code (filtering happens in extract_references)."""
        code = '''
        # config.get("commented_out")
        real = config.get("real_key")
        '''
        matches = CONFIG_PATTERN.findall(code)
        # Raw pattern matches both - comment filtering happens in extract_references
        assert "real_key" in matches
        assert "commented_out" in matches

    def test_pattern_ignores_other_methods(self):
        """Test that patterns don't match other get methods."""
        code = '''
        other.get("something")
        dictionary.get("key")
        configuration.get("value")
        '''
        config_matches = CONFIG_PATTERN.findall(code)
        integration_matches = INTEGRATIONS_PATTERN.findall(code)
        assert config_matches == []
        assert integration_matches == []


class TestCommentFiltering:
    """Test comment filtering in extract_references."""

    @pytest.fixture
    def scanner(self):
        """Create scanner with mock db."""
        mock_db = MagicMock()
        return SDKReferenceScanner(mock_db)

    def test_ignores_full_line_comment(self, scanner):
        """Test that full-line commented SDK calls are ignored."""
        code = '''
        # config.get("commented_out")
        real = config.get("real_key")
        '''
        config_refs, _ = scanner.extract_references(code)
        assert config_refs == {"real_key"}
        assert "commented_out" not in config_refs

    def test_ignores_indented_comment(self, scanner):
        """Test that indented commented SDK calls are ignored."""
        code = '''
            # This is a comment with config.get("ignored")
        key = config.get("active")
        '''
        config_refs, _ = scanner.extract_references(code)
        assert config_refs == {"active"}

    def test_captures_code_before_inline_comment(self, scanner):
        """Test that SDK calls before inline comments are captured."""
        code = '''key = config.get("api_key")  # Get the API key'''
        config_refs, _ = scanner.extract_references(code)
        assert config_refs == {"api_key"}

    def test_ignores_sdk_in_inline_comment(self, scanner):
        """Test that SDK calls in inline comments are ignored."""
        code = '''x = 1  # config.get("old_key") was removed'''
        config_refs, _ = scanner.extract_references(code)
        assert config_refs == set()

    def test_hash_inside_string_not_comment(self, scanner):
        """Test that # inside string values is not treated as comment."""
        code = '''key = config.get("api#key")'''
        config_refs, _ = scanner.extract_references(code)
        assert config_refs == {"api#key"}

    def test_hash_in_key_with_following_code(self, scanner):
        """Test hash in key followed by real code on same line."""
        # This is contrived but tests the parser
        code = '''x = config.get("key#1"); y = config.get("key2")'''
        config_refs, _ = scanner.extract_references(code)
        assert "key#1" in config_refs
        assert "key2" in config_refs

    def test_multiple_lines_mixed_comments(self, scanner):
        """Test multiple lines with mix of comments and real code."""
        code = '''
        # Old code: config.get("deprecated")
        active = config.get("active_key")
        # integrations.get("OldIntegration")
        halo = integrations.get("HaloPSA")
        final = config.get("final_key")  # This one is important
        '''
        config_refs, integration_refs = scanner.extract_references(code)
        assert config_refs == {"active_key", "final_key"}
        assert integration_refs == {"HaloPSA"}
        assert "deprecated" not in config_refs
        assert "OldIntegration" not in integration_refs

    def test_comment_after_complex_string(self, scanner):
        """Test comment detection after a complex string expression."""
        code = '''key = config.get("value") + "extra"  # comment with config.get("ignored")'''
        config_refs, _ = scanner.extract_references(code)
        # Only the real config.get should be captured, not the one in the comment
        assert config_refs == {"value"}

    def test_single_quotes_with_hash(self, scanner):
        """Test single quoted strings containing hash."""
        code = """key = config.get('api#secret')"""
        config_refs, _ = scanner.extract_references(code)
        assert config_refs == {"api#secret"}

    def test_integration_comment_filtering(self, scanner):
        """Test that integration calls in comments are also filtered."""
        code = '''
        # integrations.get("DisabledIntegration")
        active = integrations.get("ActiveIntegration")
        '''
        _, integration_refs = scanner.extract_references(code)
        assert integration_refs == {"ActiveIntegration"}
        assert "DisabledIntegration" not in integration_refs


class TestFindCommentPosition:
    """Test the _find_comment_position helper method."""

    @pytest.fixture
    def scanner(self):
        """Create scanner with mock db."""
        mock_db = MagicMock()
        return SDKReferenceScanner(mock_db)

    def test_no_comment(self, scanner):
        """Test line with no comment."""
        assert scanner._find_comment_position("x = 1") is None

    def test_comment_at_start(self, scanner):
        """Test comment at start of line."""
        assert scanner._find_comment_position("# comment") == 0

    def test_comment_after_code(self, scanner):
        """Test comment after code."""
        assert scanner._find_comment_position("x = 1  # comment") == 7

    def test_hash_in_double_quoted_string(self, scanner):
        """Test hash inside double-quoted string is not a comment."""
        assert scanner._find_comment_position('x = "a#b"') is None

    def test_hash_in_single_quoted_string(self, scanner):
        """Test hash inside single-quoted string is not a comment."""
        assert scanner._find_comment_position("x = 'a#b'") is None

    def test_hash_after_string(self, scanner):
        """Test hash after a string is a comment."""
        result = scanner._find_comment_position('x = "value"  # comment')
        assert result == 13

    def test_escaped_quote(self, scanner):
        """Test escaped quote doesn't break string detection."""
        # String: "test\"value" followed by comment
        result = scanner._find_comment_position(r'x = "test\"val"  # note')
        assert result is not None
        assert result > 0


class TestExtractReferences:
    """Test the extract_references method."""

    @pytest.fixture
    def scanner(self):
        """Create scanner with mock db."""
        mock_db = MagicMock()
        return SDKReferenceScanner(mock_db)

    def test_extract_empty_file(self, scanner):
        """Test extracting from empty file."""
        config_refs, integration_refs = scanner.extract_references("")
        assert config_refs == set()
        assert integration_refs == set()

    def test_extract_no_sdk_calls(self, scanner):
        """Test extracting from file with no SDK calls."""
        code = '''
        def hello():
            return "world"
        '''
        config_refs, integration_refs = scanner.extract_references(code)
        assert config_refs == set()
        assert integration_refs == set()

    def test_extract_mixed_calls(self, scanner):
        """Test extracting both config and integration calls."""
        code = '''
        from bifrost import config, integrations

        async def my_workflow():
            api_key = await config.get("api_key")
            url = await config.get("base_url")
            halo = await integrations.get("HaloPSA")
            return api_key, url, halo
        '''
        config_refs, integration_refs = scanner.extract_references(code)
        assert config_refs == {"api_key", "base_url"}
        assert integration_refs == {"HaloPSA"}

    def test_extract_deduplicates(self, scanner):
        """Test that duplicate references are deduplicated."""
        code = '''
        key1 = await config.get("api_key")
        key2 = await config.get("api_key")  # Same key again
        '''
        config_refs, integration_refs = scanner.extract_references(code)
        assert config_refs == {"api_key"}


class TestFindLineNumber:
    """Test the _find_line_number method."""

    @pytest.fixture
    def scanner(self):
        """Create scanner with mock db."""
        mock_db = MagicMock()
        return SDKReferenceScanner(mock_db)

    def test_find_line_number_basic(self, scanner):
        """Test finding line number."""
        lines = [
            "from bifrost import config",
            "",
            "key = config.get('api_key')",
        ]
        line_num = scanner._find_line_number(lines, "config.get", "api_key")
        assert line_num == 3

    def test_find_line_number_with_await(self, scanner):
        """Test finding line number with await."""
        lines = [
            "from bifrost import config",
            "key = await config.get('api_key')",
        ]
        line_num = scanner._find_line_number(lines, "config.get", "api_key")
        assert line_num == 2

    def test_find_line_number_not_found(self, scanner):
        """Test default to line 1 when not found."""
        lines = ["some code", "more code"]
        line_num = scanner._find_line_number(lines, "config.get", "missing")
        assert line_num == 1


class TestScanFile:
    """Test the scan_file method."""

    @pytest.fixture
    def scanner(self):
        """Create scanner with mock db."""
        mock_db = AsyncMock()
        return SDKReferenceScanner(mock_db)

    @pytest.mark.asyncio
    async def test_scan_file_no_issues(self, scanner):
        """Test scanning file with all valid references."""
        code = '''
        key = await config.get("api_key")
        halo = await integrations.get("HaloPSA")
        '''

        # Mock database to return the keys as existing
        scanner.get_all_config_keys = AsyncMock(return_value={"api_key"})
        scanner.get_all_mapped_integrations = AsyncMock(return_value={"HaloPSA"})

        issues = await scanner.scan_file("test.py", code)
        assert issues == []

    @pytest.mark.asyncio
    async def test_scan_file_missing_config(self, scanner):
        """Test scanning file with missing config."""
        code = '''key = await config.get("missing_key")'''

        scanner.get_all_config_keys = AsyncMock(return_value={"other_key"})
        scanner.get_all_mapped_integrations = AsyncMock(return_value=set())

        issues = await scanner.scan_file("test.py", code)

        assert len(issues) == 1
        assert issues[0].file_path == "test.py"
        assert issues[0].issue_type == "config"
        assert issues[0].key == "missing_key"

    @pytest.mark.asyncio
    async def test_scan_file_missing_integration(self, scanner):
        """Test scanning file with missing integration."""
        code = '''halo = await integrations.get("UnknownIntegration")'''

        scanner.get_all_config_keys = AsyncMock(return_value=set())
        scanner.get_all_mapped_integrations = AsyncMock(return_value={"HaloPSA"})

        issues = await scanner.scan_file("test.py", code)

        assert len(issues) == 1
        assert issues[0].file_path == "test.py"
        assert issues[0].issue_type == "integration"
        assert issues[0].key == "UnknownIntegration"

    @pytest.mark.asyncio
    async def test_scan_file_multiple_issues(self, scanner):
        """Test scanning file with multiple issues."""
        code = '''
        key1 = await config.get("missing_config")
        key2 = await config.get("another_missing")
        halo = await integrations.get("MissingIntegration")
        '''

        scanner.get_all_config_keys = AsyncMock(return_value=set())
        scanner.get_all_mapped_integrations = AsyncMock(return_value=set())

        issues = await scanner.scan_file("test.py", code)

        assert len(issues) == 3
        issue_keys = {i.key for i in issues}
        assert issue_keys == {"missing_config", "another_missing", "MissingIntegration"}

    @pytest.mark.asyncio
    async def test_scan_file_empty(self, scanner):
        """Test scanning empty file."""
        issues = await scanner.scan_file("test.py", "")
        assert issues == []

    @pytest.mark.asyncio
    async def test_scan_file_no_sdk_calls(self, scanner):
        """Test scanning file with no SDK calls."""
        code = '''
        def hello():
            return "world"
        '''
        issues = await scanner.scan_file("test.py", code)
        assert issues == []


class TestSDKIssueDataclass:
    """Test the SDKIssue dataclass."""

    def test_sdk_issue_creation(self):
        """Test creating SDKIssue."""
        issue = SDKIssue(
            file_path="workflows/my_workflow.py",
            line_number=42,
            issue_type="config",
            key="api_key",
        )
        assert issue.file_path == "workflows/my_workflow.py"
        assert issue.line_number == 42
        assert issue.issue_type == "config"
        assert issue.key == "api_key"

    def test_sdk_issue_integration_type(self):
        """Test SDKIssue with integration type."""
        issue = SDKIssue(
            file_path="test.py",
            line_number=10,
            issue_type="integration",
            key="HaloPSA",
        )
        assert issue.issue_type == "integration"
        assert issue.key == "HaloPSA"
