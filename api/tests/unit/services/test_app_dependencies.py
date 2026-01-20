"""
Unit tests for app dependency parsing.
"""

from uuid import UUID

from src.services.app_dependencies import parse_dependencies


class TestParseDependencies:
    """Tests for parse_dependencies function."""

    def test_parses_single_use_workflow(self):
        """Single useWorkflow call is parsed."""
        source = "const w = useWorkflow('550e8400-e29b-41d4-a716-446655440000');"

        deps = parse_dependencies(source)

        assert len(deps) == 1
        assert deps[0][0] == "workflow"
        assert deps[0][1] == UUID("550e8400-e29b-41d4-a716-446655440000")

    def test_parses_double_quoted_uuid(self):
        """Double-quoted UUIDs are also parsed."""
        source = 'const w = useWorkflow("550e8400-e29b-41d4-a716-446655440000");'

        deps = parse_dependencies(source)

        assert len(deps) == 1

    def test_parses_multiple_workflows(self):
        """Multiple useWorkflow calls are all parsed."""
        source = """
const w1 = useWorkflow('11111111-1111-1111-1111-111111111111');
const w2 = useWorkflow('22222222-2222-2222-2222-222222222222');
"""
        deps = parse_dependencies(source)

        assert len(deps) == 2

    def test_deduplicates_same_uuid(self):
        """Same UUID used multiple times is deduplicated."""
        source = """
const w1 = useWorkflow('550e8400-e29b-41d4-a716-446655440000');
const w2 = useWorkflow('550e8400-e29b-41d4-a716-446655440000');
"""
        deps = parse_dependencies(source)

        assert len(deps) == 1

    def test_ignores_non_uuid_strings(self):
        """Non-UUID strings in useWorkflow are ignored."""
        source = "const w = useWorkflow('not-a-valid-uuid');"

        deps = parse_dependencies(source)

        assert len(deps) == 0

    def test_ignores_portable_refs(self):
        """Portable refs (not UUIDs) are ignored by dependency parser."""
        source = "const w = useWorkflow('workflows/test.py::my_func');"

        deps = parse_dependencies(source)

        assert len(deps) == 0

    def test_empty_source(self):
        """Empty source returns empty list."""
        deps = parse_dependencies("")

        assert len(deps) == 0

    def test_no_use_workflow_calls(self):
        """Source without useWorkflow returns empty list."""
        source = "const x = 1; const y = 2;"

        deps = parse_dependencies(source)

        assert len(deps) == 0
