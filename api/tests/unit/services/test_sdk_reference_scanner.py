import pytest
from unittest.mock import AsyncMock, MagicMock

from src.services.sdk_reference_scanner import SDKReferenceScanner, SDKIssue


@pytest.fixture
def scanner():
    return SDKReferenceScanner(MagicMock())


class TestExtractReferences:

    def test_config_get_without_default(self, scanner):
        config_keys, integration_names = scanner.extract_references('config.get("my_key")')
        assert config_keys == {"my_key"}
        assert integration_names == set()

    def test_config_get_with_default_is_skipped(self, scanner):
        config_keys, integration_names = scanner.extract_references('config.get("my_key", "default")')
        assert config_keys == set()
        assert integration_names == set()

    def test_await_config_get(self, scanner):
        config_keys, _ = scanner.extract_references('await config.get("async_key")')
        assert config_keys == {"async_key"}

    def test_integrations_get(self, scanner):
        _, integration_names = scanner.extract_references('integrations.get("slack")')
        assert integration_names == {"slack"}

    def test_await_integrations_get(self, scanner):
        _, integration_names = scanner.extract_references('await integrations.get("teams")')
        assert integration_names == {"teams"}

    def test_multiple_calls_returns_all_unique(self, scanner):
        code = """
key1 = config.get("a")
key2 = await config.get("b")
key3 = config.get("a")
i1 = integrations.get("slack")
i2 = await integrations.get("teams")
i3 = integrations.get("slack")
"""
        config_keys, integration_names = scanner.extract_references(code)
        assert config_keys == {"a", "b"}
        assert integration_names == {"slack", "teams"}

    def test_commented_out_call_not_captured(self, scanner):
        code = '# config.get("ignored")\nreal = config.get("kept")'
        config_keys, _ = scanner.extract_references(code)
        assert config_keys == {"kept"}
        assert "ignored" not in config_keys

    def test_no_sdk_calls(self, scanner):
        config_keys, integration_names = scanner.extract_references("x = 1\nprint('hello')")
        assert config_keys == set()
        assert integration_names == set()

    def test_call_inside_string(self, scanner):
        code = """print('config.get("x")')"""
        config_keys, _ = scanner.extract_references(code)
        # The regex matches inside strings because it's a simple pattern matcher.
        # extract_references only filters comments, not string literals.
        # Verify actual behavior: the regex WILL match inside the string.
        assert config_keys == {"x"}

    def test_empty_content(self, scanner):
        config_keys, integration_names = scanner.extract_references("")
        assert config_keys == set()
        assert integration_names == set()

    def test_mixed_config_and_integrations(self, scanner):
        code = """
url = await config.get("base_url")
opt = config.get("optional", "fallback")
halo = await integrations.get("HaloPSA")
"""
        config_keys, integration_names = scanner.extract_references(code)
        assert config_keys == {"base_url"}
        assert integration_names == {"HaloPSA"}

    def test_deduplicates(self, scanner):
        code = 'config.get("dup")\nconfig.get("dup")'
        config_keys, _ = scanner.extract_references(code)
        assert config_keys == {"dup"}


class TestFindCommentPosition:

    def test_code_with_comment(self, scanner):
        pos = scanner._find_comment_position("code # comment")
        assert pos == 5

    def test_no_comment(self, scanner):
        assert scanner._find_comment_position("no comment here") is None

    def test_hash_inside_double_quoted_string(self, scanner):
        assert scanner._find_comment_position('x = "has # inside string"') is None

    def test_hash_inside_single_quote_then_real_comment(self, scanner):
        line = "x = 'has # inside' # real comment"
        pos = scanner._find_comment_position(line)
        assert pos == 19

    def test_empty_string(self, scanner):
        assert scanner._find_comment_position("") is None

    def test_comment_at_start(self, scanner):
        assert scanner._find_comment_position("# full line comment") == 0

    def test_hash_in_single_quoted_string(self, scanner):
        assert scanner._find_comment_position("x = 'a#b'") is None

    def test_hash_after_double_quoted_string(self, scanner):
        pos = scanner._find_comment_position('x = "value"  # comment')
        assert pos == 13


class TestFindLineNumber:

    def test_finds_correct_line(self, scanner):
        lines = [
            "from bifrost import config",
            "",
            "key = config.get('my_key')",
        ]
        assert scanner._find_line_number(lines, "config.get", "my_key") == 3

    def test_call_not_found_returns_1(self, scanner):
        lines = ["some code", "more code"]
        assert scanner._find_line_number(lines, "config.get", "missing") == 1

    def test_finds_integration_call(self, scanner):
        lines = [
            "import stuff",
            'halo = integrations.get("HaloPSA")',
        ]
        assert scanner._find_line_number(lines, "integrations.get", "HaloPSA") == 2

    def test_finds_first_occurrence(self, scanner):
        lines = [
            'config.get("dup")',
            'config.get("dup")',
        ]
        assert scanner._find_line_number(lines, "config.get", "dup") == 1


class TestScanFile:

    @pytest.fixture
    def async_scanner(self):
        return SDKReferenceScanner(AsyncMock())

    @pytest.mark.asyncio
    async def test_no_issues_when_all_exist(self, async_scanner):
        code = 'key = await config.get("api_key")\nhalo = await integrations.get("HaloPSA")'
        async_scanner.get_all_config_keys = AsyncMock(return_value={"api_key"})
        async_scanner.get_all_mapped_integrations = AsyncMock(return_value={"HaloPSA"})
        issues = await async_scanner.scan_file("test.py", code)
        assert issues == []

    @pytest.mark.asyncio
    async def test_missing_config(self, async_scanner):
        code = 'key = await config.get("missing_key")'
        async_scanner.get_all_config_keys = AsyncMock(return_value=set())
        async_scanner.get_all_mapped_integrations = AsyncMock(return_value=set())
        issues = await async_scanner.scan_file("test.py", code)
        assert len(issues) == 1
        assert issues[0].issue_type == "config"
        assert issues[0].key == "missing_key"
        assert issues[0].file_path == "test.py"

    @pytest.mark.asyncio
    async def test_missing_integration(self, async_scanner):
        code = 'halo = await integrations.get("Unknown")'
        async_scanner.get_all_config_keys = AsyncMock(return_value=set())
        async_scanner.get_all_mapped_integrations = AsyncMock(return_value=set())
        issues = await async_scanner.scan_file("test.py", code)
        assert len(issues) == 1
        assert issues[0].issue_type == "integration"
        assert issues[0].key == "Unknown"

    @pytest.mark.asyncio
    async def test_empty_file(self, async_scanner):
        issues = await async_scanner.scan_file("test.py", "")
        assert issues == []

    @pytest.mark.asyncio
    async def test_no_sdk_calls(self, async_scanner):
        issues = await async_scanner.scan_file("test.py", "x = 1")
        assert issues == []

    @pytest.mark.asyncio
    async def test_multiple_issues(self, async_scanner):
        code = """
key = config.get("miss_cfg")
i = integrations.get("miss_int")
"""
        async_scanner.get_all_config_keys = AsyncMock(return_value=set())
        async_scanner.get_all_mapped_integrations = AsyncMock(return_value=set())
        issues = await async_scanner.scan_file("wf.py", code)
        assert len(issues) == 2
        keys = {i.key for i in issues}
        assert keys == {"miss_cfg", "miss_int"}


class TestSDKIssue:

    def test_creation(self):
        issue = SDKIssue(file_path="wf.py", line_number=42, issue_type="config", key="api_key")
        assert issue.file_path == "wf.py"
        assert issue.line_number == 42
        assert issue.issue_type == "config"
        assert issue.key == "api_key"

    def test_integration_type(self):
        issue = SDKIssue(file_path="t.py", line_number=10, issue_type="integration", key="HaloPSA")
        assert issue.issue_type == "integration"
        assert issue.key == "HaloPSA"
