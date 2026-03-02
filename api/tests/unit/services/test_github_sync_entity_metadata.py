"""Tests for entity metadata extraction from sync files."""
from src.services.github_sync_entity_metadata import (
    extract_entity_metadata,
)


class TestExtractEntityMetadata:
    """Tests for extract_entity_metadata function."""

    def test_form_extracts_name(self):
        """Form YAML extracts name as display_name."""
        path = "forms/abc123.form.yaml"
        content = b'name: Customer Intake\nid: abc123\n'

        result = extract_entity_metadata(path, content)

        assert result.entity_type == "form"
        assert result.display_name == "Customer Intake"
        assert result.parent_slug is None

    def test_agent_extracts_name(self):
        """Agent YAML extracts name as display_name."""
        path = "agents/xyz789.agent.yaml"
        content = b'name: Support Bot\nid: xyz789\n'

        result = extract_entity_metadata(path, content)

        assert result.entity_type == "agent"
        assert result.display_name == "Support Bot"
        assert result.parent_slug is None

    def test_app_file_extracts_parent_slug(self):
        """App code file extracts parent slug when app_prefixes provided."""
        path = "apps/dashboard/pages/index.tsx"
        content = b'export default function Home() { return <div>Home</div> }'

        result = extract_entity_metadata(path, content, app_prefixes={"apps/dashboard"})

        assert result.entity_type == "app_file"
        assert result.display_name == "pages/index.tsx"
        assert result.parent_slug == "dashboard"

    def test_app_file_without_prefixes_detected_as_workflow(self):
        """Without app_prefixes, .py files under apps/ are detected as workflow."""
        path = "apps/dashboard/utils.py"
        content = b'def helper(): pass'

        result = extract_entity_metadata(path, content)

        assert result.entity_type == "workflow"

    def test_app_file_custom_prefix(self):
        """Custom non-apps/ prefix works for app detection."""
        path = "custom/my-app/pages/home.tsx"
        content = b'export default function Home() { return <div>Home</div> }'

        result = extract_entity_metadata(path, content, app_prefixes={"custom/my-app"})

        assert result.entity_type == "app_file"
        assert result.display_name == "pages/home.tsx"
        assert result.parent_slug == "my-app"

    def test_workflow_uses_filename(self):
        """Workflow uses filename as display_name."""
        path = "workflows/process_payment.py"
        content = b'@workflow\ndef process_payment(): pass'

        result = extract_entity_metadata(path, content)

        assert result.entity_type == "workflow"
        assert result.display_name == "process_payment.py"
        assert result.parent_slug is None

    def test_unknown_file_returns_filename(self):
        """Unknown file type returns filename as display_name."""
        path = "random/file.txt"
        content = b'some content'

        result = extract_entity_metadata(path, content)

        assert result.entity_type is None
        assert result.display_name == "file.txt"
        assert result.parent_slug is None

    def test_invalid_yaml_uses_filename(self):
        """Invalid YAML falls back to filename."""
        path = "forms/broken.form.yaml"
        content = b': invalid: yaml: content'

        result = extract_entity_metadata(path, content)

        assert result.entity_type == "form"
        assert result.display_name == "broken.form.yaml"
