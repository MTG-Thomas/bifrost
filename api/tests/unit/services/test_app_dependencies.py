"""
Unit tests for app dependency parsing.
"""

from src.services.app_dependencies import parse_dependencies


class TestParseDependencies:
    """Tests for parse_dependencies function."""

    def test_parses_single_use_workflow_with_uuid(self):
        """Single useWorkflow call with UUID is parsed."""
        source = "const w = useWorkflow('550e8400-e29b-41d4-a716-446655440000');"

        refs = parse_dependencies(source)

        assert len(refs) == 1
        assert refs[0] == "550e8400-e29b-41d4-a716-446655440000"

    def test_parses_workflow_name(self):
        """useWorkflowQuery with a workflow name is parsed."""
        source = "const { data } = useWorkflowQuery('list_csp_tenants');"

        refs = parse_dependencies(source)

        assert len(refs) == 1
        assert refs[0] == "list_csp_tenants"

    def test_parses_double_quoted_ref(self):
        """Double-quoted references are also parsed."""
        source = 'const w = useWorkflow("my_workflow");'

        refs = parse_dependencies(source)

        assert len(refs) == 1
        assert refs[0] == "my_workflow"

    def test_parses_multiple_refs(self):
        """Multiple useWorkflow calls are all parsed."""
        source = """
const w1 = useWorkflowQuery('list_tenants');
const w2 = useWorkflowMutation('create_tenant');
"""
        refs = parse_dependencies(source)

        assert len(refs) == 2
        assert "list_tenants" in refs
        assert "create_tenant" in refs

    def test_deduplicates_same_ref(self):
        """Same ref used multiple times is deduplicated."""
        source = """
const w1 = useWorkflowQuery('list_tenants');
const w2 = useWorkflowQuery('list_tenants');
"""
        refs = parse_dependencies(source)

        assert len(refs) == 1

    def test_empty_source(self):
        """Empty source returns empty list."""
        refs = parse_dependencies("")

        assert len(refs) == 0

    def test_no_use_workflow_calls(self):
        """Source without useWorkflow returns empty list."""
        source = "const x = 1; const y = 2;"

        refs = parse_dependencies(source)

        assert len(refs) == 0

    def test_parses_use_workflow_query(self):
        """useWorkflowQuery call is parsed."""
        source = "const { data } = useWorkflowQuery('550e8400-e29b-41d4-a716-446655440000');"

        refs = parse_dependencies(source)

        assert len(refs) == 1
        assert refs[0] == "550e8400-e29b-41d4-a716-446655440000"

    def test_parses_use_workflow_mutation(self):
        """useWorkflowMutation call is parsed."""
        source = "const { execute } = useWorkflowMutation('run_report');"

        refs = parse_dependencies(source)

        assert len(refs) == 1
        assert refs[0] == "run_report"

    def test_parses_mixed_hook_calls(self):
        """All three hook variants are parsed and deduplicated."""
        source = """
const q = useWorkflowQuery('query_workflow');
const m = useWorkflowMutation('mutate_workflow');
const w = useWorkflow('legacy_workflow');
"""
        refs = parse_dependencies(source)

        assert len(refs) == 3

    def test_deduplicates_across_hook_variants(self):
        """Same ref used via different hooks is deduplicated."""
        source = """
const q = useWorkflowQuery('my_workflow');
const m = useWorkflowMutation('my_workflow');
"""
        refs = parse_dependencies(source)

        assert len(refs) == 1
        assert refs[0] == "my_workflow"

    def test_parses_ref_with_spaces_around_quotes(self):
        """Whitespace between parens and quotes is handled."""
        source = "const w = useWorkflowQuery( 'my_workflow' );"

        refs = parse_dependencies(source)

        assert len(refs) == 1
        assert refs[0] == "my_workflow"

    def test_mixed_uuid_and_name_refs(self):
        """Both UUID and name refs are extracted."""
        source = """
const q = useWorkflowQuery('550e8400-e29b-41d4-a716-446655440000');
const m = useWorkflowMutation('create_tenant');
"""
        refs = parse_dependencies(source)

        assert len(refs) == 2
        assert "550e8400-e29b-41d4-a716-446655440000" in refs
        assert "create_tenant" in refs


