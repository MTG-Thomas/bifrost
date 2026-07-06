"""Tests for the _search_content pure function in src.services.editor.search."""

import os
import re

import pytest
from hypothesis import given, settings, strategies as st

from src.services.editor import search as editor_search
from src.services.editor.search import _search_content, _validate_regex_pattern


FUZZ_EXAMPLES = int(os.environ.get("BIFROST_FUZZ_EXAMPLES", "100"))
REGEX_VALIDATION_FUZZ_EXAMPLES = int(
    os.environ.get("BIFROST_REGEX_VALIDATION_FUZZ_EXAMPLES", str(FUZZ_EXAMPLES))
)
REGEX_SEARCH_FUZZ_EXAMPLES = int(
    os.environ.get("BIFROST_REGEX_SEARCH_FUZZ_EXAMPLES", "1000")
)


class TestSimpleSearch:
    """Basic text search functionality."""

    def test_finds_match_with_correct_line_number(self):
        content = "hello world"
        results = _search_content(content, "test.py", "hello", case_sensitive=False, is_regex=False)
        assert len(results) == 1
        assert results[0].line == 1
        assert results[0].column == 0
        assert results[0].match_text == "hello world"
        assert results[0].file_path == "test.py"

    def test_no_matches_returns_empty_list(self):
        content = "hello world"
        results = _search_content(content, "test.py", "missing", case_sensitive=False, is_regex=False)
        assert results == []

    def test_empty_content_returns_empty(self):
        results = _search_content("", "test.py", "hello", case_sensitive=False, is_regex=False)
        assert results == []

    @pytest.mark.property
    @settings(max_examples=FUZZ_EXAMPLES, deadline=200)
    @given(
        content=st.text(max_size=200),
        query=st.text(min_size=1, max_size=30),
        case_sensitive=st.booleans(),
    )
    def test_literal_search_matches_python_substring_semantics(
        self,
        content: str,
        query: str,
        case_sensitive: bool,
    ):
        results = _search_content(
            content,
            "test.py",
            query,
            case_sensitive=case_sensitive,
            is_regex=False,
        )

        flags = 0 if case_sensitive else re.IGNORECASE
        expected_match_count = sum(
            1
            for line in content.split("\n")
            for _ in re.finditer(re.escape(query), line, flags)
        )

        assert len(results) == expected_match_count
        assert all(result.match_text in content.split("\n") for result in results)


class TestCaseSensitivity:
    """Case sensitivity behavior."""

    def test_case_insensitive_finds_different_case(self):
        content = "Hello World"
        results = _search_content(content, "test.py", "hello", case_sensitive=False, is_regex=False)
        assert len(results) == 1

    def test_case_sensitive_misses_different_case(self):
        content = "Hello World"
        results = _search_content(content, "test.py", "hello", case_sensitive=True, is_regex=False)
        assert len(results) == 0

    def test_case_sensitive_finds_exact_case(self):
        content = "Hello World"
        results = _search_content(content, "test.py", "Hello", case_sensitive=True, is_regex=False)
        assert len(results) == 1


class TestRegexSearch:
    """Regex pattern search functionality."""

    def test_regex_digit_pattern(self):
        content = "line with 42 numbers"
        results = _search_content(content, "test.py", r"\d+", case_sensitive=False, is_regex=True)
        assert len(results) == 1
        assert results[0].column == 10

    def test_regex_word_boundary(self):
        content = "foo bar baz"
        results = _search_content(content, "test.py", r"\bbar\b", case_sensitive=False, is_regex=True)
        assert len(results) == 1
        assert results[0].column == 4

    def test_invalid_regex_returns_empty(self):
        content = "hello world"
        results = _search_content(content, "test.py", "[invalid", case_sensitive=False, is_regex=True)
        assert results == []

    @pytest.mark.property
    @settings(max_examples=REGEX_VALIDATION_FUZZ_EXAMPLES, deadline=200)
    @given(
        pattern=st.text(max_size=80),
    )
    def test_regex_validation_accepts_or_rejects_cleanly(
        self,
        pattern: str,
    ):
        try:
            _validate_regex_pattern(pattern)
        except ValueError as exc:
            assert "nested quantifiers" in str(exc) or "exceeds" in str(exc)
        except Exception as exc:
            # The stdlib parser can reject malformed generated patterns. Those
            # are acceptable controlled rejections for this validation target.
            assert exc.__class__.__module__.startswith("re")

    @pytest.mark.property
    @settings(max_examples=REGEX_SEARCH_FUZZ_EXAMPLES, deadline=None)
    @given(
        content=st.text(max_size=120),
        pattern=st.sampled_from(
            [
                r"\w+",
                r"\d+",
                r"\s+",
                r"[A-Za-z_][A-Za-z0-9_]*",
                r"(?:foo|bar|baz)",
                r"^.{0,20}$",
                r"\b(?:GET|POST|PUT|DELETE)\b",
            ]
        ),
        case_sensitive=st.booleans(),
    )
    def test_regex_search_with_safe_patterns_returns_results(
        self,
        content: str,
        pattern: str,
        case_sensitive: bool,
    ):
        results = _search_content(
            content,
            "test.py",
            pattern,
            case_sensitive=case_sensitive,
            is_regex=True,
        )
        assert isinstance(results, list)
        assert all(result.file_path == "test.py" for result in results)

    def test_regex_search_uses_timeout_capable_engine(self, monkeypatch):
        """Explicit regex mode must bound user-provided pattern execution."""
        observed = {}

        class FakeRegex:
            def finditer(self, line, *, timeout):
                observed["line"] = line
                observed["timeout"] = timeout
                return iter(())

        def fake_compile(pattern, flags):
            observed["pattern"] = pattern
            observed["flags"] = flags
            return FakeRegex()

        monkeypatch.setattr(editor_search.bounded_regex, "compile", fake_compile)

        results = _search_content(
            "hello world",
            "test.py",
            r"hello|world",
            case_sensitive=False,
            is_regex=True,
        )

        assert results == []
        assert observed["pattern"] == r"hello|world"
        assert observed["line"] == "hello world"
        assert observed["timeout"] == editor_search.REGEX_SEARCH_TIMEOUT_SECONDS

    def test_regex_timeout_returns_empty(self, monkeypatch):
        class FakeRegex:
            def finditer(self, line, *, timeout):
                raise TimeoutError("timed out")

        monkeypatch.setattr(
            editor_search.bounded_regex,
            "compile",
            lambda pattern, flags: FakeRegex(),
        )

        results = _search_content(
            "hello world",
            "test.py",
            r"hello|world",
            case_sensitive=False,
            is_regex=True,
        )

        assert results == []

    def test_nested_quantifier_regex_rejected_before_search(self):
        content = "a" * 1000
        risky_pattern = "".join(["(", "a", "+", ")", "+", "$"])
        with pytest.raises(ValueError, match="nested quantifiers"):
            _validate_regex_pattern(risky_pattern)
        with pytest.raises(ValueError, match="nested quantifiers"):
            _search_content(content, "test.py", risky_pattern, case_sensitive=False, is_regex=True)

    def test_ambiguous_quantified_alternation_rejected_before_search(self):
        risky_pattern = "".join(["(", "a", "|", "a", ")", "+"])
        with pytest.raises(ValueError, match="nested quantifiers"):
            _validate_regex_pattern(risky_pattern)

    @pytest.mark.parametrize(
        "pattern",
        [
            r"(?:async\s+)?def",
            r"(\d+)?",
            r"(http|https)?",
            r"(?:foo|bar)+",
            r"(?:[a-z]+\d+)?",
        ],
    )
    def test_benign_grouped_regex_patterns_are_accepted(self, pattern):
        _validate_regex_pattern(pattern)

    def test_regex_pattern_length_limit(self):
        with pytest.raises(ValueError, match="exceeds"):
            _validate_regex_pattern("a" * 513)

    def test_special_regex_chars_escaped_in_literal_search(self):
        """When is_regex=False, special characters like . and + should be escaped."""
        content = "file.txt is here"
        results = _search_content(content, "test.py", "file.txt", case_sensitive=False, is_regex=False)
        assert len(results) == 1

        # "file.txt" as literal should NOT match "fileXtxt"
        content_no_dot = "fileXtxt is here"
        results = _search_content(content_no_dot, "test.py", "file.txt", case_sensitive=False, is_regex=False)
        assert len(results) == 0

    def test_parentheses_escaped_in_literal_search(self):
        """Parentheses should be treated as literal characters when is_regex=False."""
        content = "print(hello)"
        results = _search_content(content, "test.py", "print(hello)", case_sensitive=False, is_regex=False)
        assert len(results) == 1


class TestMultipleMatches:
    """Multiple match scenarios."""

    def test_matches_on_different_lines(self):
        content = "first match\nsecond line\nthird match"
        results = _search_content(content, "test.py", "match", case_sensitive=False, is_regex=False)
        assert len(results) == 2
        assert results[0].line == 1
        assert results[1].line == 3

    def test_multiple_matches_on_same_line(self):
        content = "foo bar foo baz foo"
        results = _search_content(content, "test.py", "foo", case_sensitive=False, is_regex=False)
        assert len(results) == 3
        assert results[0].column == 0
        assert results[1].column == 8
        assert results[2].column == 16
        # All should have the same line number
        assert all(r.line == 1 for r in results)


class TestContextLines:
    """Context before and after match lines."""

    def test_context_before_none_for_first_line(self):
        content = "first line\nsecond line\nthird line"
        results = _search_content(content, "test.py", "first", case_sensitive=False, is_regex=False)
        assert len(results) == 1
        assert results[0].context_before is None
        assert results[0].context_after == "second line"

    def test_context_after_none_for_last_line(self):
        content = "first line\nsecond line\nthird line"
        results = _search_content(content, "test.py", "third", case_sensitive=False, is_regex=False)
        assert len(results) == 1
        assert results[0].context_before == "second line"
        assert results[0].context_after is None

    def test_middle_line_has_both_contexts(self):
        content = "first line\nsecond line\nthird line"
        results = _search_content(content, "test.py", "second", case_sensitive=False, is_regex=False)
        assert len(results) == 1
        assert results[0].context_before == "first line"
        assert results[0].context_after == "third line"

    def test_two_line_file_first_line(self):
        content = "alpha\nbeta"
        results = _search_content(content, "test.py", "alpha", case_sensitive=False, is_regex=False)
        assert len(results) == 1
        assert results[0].context_before is None
        assert results[0].context_after == "beta"

    def test_two_line_file_second_line(self):
        content = "alpha\nbeta"
        results = _search_content(content, "test.py", "beta", case_sensitive=False, is_regex=False)
        assert len(results) == 1
        assert results[0].context_before == "alpha"
        assert results[0].context_after is None

    def test_single_line_file_no_context(self):
        content = "only line"
        results = _search_content(content, "test.py", "only", case_sensitive=False, is_regex=False)
        assert len(results) == 1
        assert results[0].context_before is None
        assert results[0].context_after is None
