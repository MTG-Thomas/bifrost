"""Tests for manifest parser."""
import pytest
from uuid import uuid4

import yaml


@pytest.fixture
def sample_manifest():
    """A valid manifest dict."""
    org_id = str(uuid4())
    role_id = str(uuid4())
    wf_id = str(uuid4())
    form_id = str(uuid4())
    return {
        "organizations": [{"id": org_id, "name": "TestOrg"}],
        "roles": [{"id": role_id, "name": "admin", "organization_id": org_id}],
        "workflows": {
            "my_workflow": {
                "id": wf_id,
                "path": "workflows/my_workflow.py",
                "function_name": "my_workflow",
                "type": "workflow",
                "organization_id": org_id,
                "roles": [role_id],
                "access_level": "role_based",
                "endpoint_enabled": False,
                "timeout_seconds": 1800,
            },
        },
        "forms": {
            "my_form": {
                "id": form_id,
                "path": "forms/my_form.form.yaml",
                "organization_id": org_id,
                "roles": [role_id],
                "access_level": "role_based",
            },
        },
        "agents": {},
        "apps": {},
    }


def test_parse_manifest_from_yaml(sample_manifest):
    """Parse a YAML string into a Manifest object."""
    from src.services.manifest import parse_manifest

    yaml_str = yaml.dump(sample_manifest, default_flow_style=False)
    manifest = parse_manifest(yaml_str)
    assert "my_workflow" in manifest.workflows
    assert manifest.workflows["my_workflow"].path == "workflows/my_workflow.py"
    assert manifest.workflows["my_workflow"].function_name == "my_workflow"
    assert manifest.workflows["my_workflow"].type == "workflow"


def test_serialize_manifest(sample_manifest):
    """Serialize a Manifest back to YAML string."""
    from src.services.manifest import parse_manifest, serialize_manifest

    yaml_str = yaml.dump(sample_manifest, default_flow_style=False)
    manifest = parse_manifest(yaml_str)
    output = serialize_manifest(manifest)
    # Should be valid YAML
    reparsed = yaml.safe_load(output)
    assert "workflows" in reparsed
    assert "my_workflow" in reparsed["workflows"]


def test_serialize_manifest_round_trip_stability(sample_manifest):
    """Serialize → parse → serialize should produce identical output (no false conflicts)."""
    from src.services.manifest import parse_manifest, serialize_manifest

    yaml_str = yaml.dump(sample_manifest, default_flow_style=False)
    manifest = parse_manifest(yaml_str)
    output1 = serialize_manifest(manifest)
    manifest2 = parse_manifest(output1)
    output2 = serialize_manifest(manifest2)
    assert output1 == output2, "Round-trip serialization must be stable"


def test_serialize_manifest_excludes_defaults():
    """Default-valued fields should be omitted from serialized YAML."""
    from src.services.manifest import parse_manifest, serialize_manifest

    yaml_str = """
workflows:
  wf1:
    id: "11111111-1111-1111-1111-111111111111"
    path: workflows/wf1.py
    function_name: wf1
"""
    manifest = parse_manifest(yaml_str)
    output = serialize_manifest(manifest)
    data = yaml.safe_load(output)
    wf = data["workflows"]["wf1"]
    # Required fields present
    assert wf["id"] == "11111111-1111-1111-1111-111111111111"
    assert wf["path"] == "workflows/wf1.py"
    assert wf["function_name"] == "wf1"
    # Default-valued fields should be absent
    assert "type" not in wf  # default is "workflow"
    assert "access_level" not in wf  # default is "role_based"
    assert "endpoint_enabled" not in wf  # default is False
    assert "timeout_seconds" not in wf  # default is 1800
    assert "roles" not in wf  # default is []
    assert "tags" not in wf  # default is []
    assert "organization_id" not in wf  # default is None


def test_validate_manifest_broken_ref(sample_manifest):
    """Detect broken cross-references."""
    from src.services.manifest import parse_manifest, validate_manifest

    # Form references a workflow UUID that exists — should be fine
    yaml_str = yaml.dump(sample_manifest, default_flow_style=False)
    manifest = parse_manifest(yaml_str)
    errors = validate_manifest(manifest)
    assert len(errors) == 0


def test_validate_manifest_missing_org(sample_manifest):
    """Detect reference to non-existent organization."""
    from src.services.manifest import parse_manifest, validate_manifest

    sample_manifest["workflows"]["my_workflow"]["organization_id"] = str(uuid4())
    yaml_str = yaml.dump(sample_manifest, default_flow_style=False)
    manifest = parse_manifest(yaml_str)
    errors = validate_manifest(manifest)
    assert any("organization" in e.lower() for e in errors)


def test_validate_manifest_missing_role(sample_manifest):
    """Detect reference to non-existent role."""
    from src.services.manifest import parse_manifest, validate_manifest

    sample_manifest["workflows"]["my_workflow"]["roles"] = [str(uuid4())]
    yaml_str = yaml.dump(sample_manifest, default_flow_style=False)
    manifest = parse_manifest(yaml_str)
    errors = validate_manifest(manifest)
    assert any("role" in e.lower() for e in errors)


def test_empty_manifest():
    """Empty manifest should parse without error."""
    from src.services.manifest import parse_manifest

    manifest = parse_manifest("")
    assert len(manifest.workflows) == 0
    assert len(manifest.forms) == 0


def test_get_entity_ids():
    """Get all entity UUIDs from manifest."""
    from src.services.manifest import parse_manifest, get_all_entity_ids

    yaml_str = """
workflows:
  wf1:
    id: "11111111-1111-1111-1111-111111111111"
    path: workflows/wf1.py
    function_name: wf1
    type: workflow
forms:
  form1:
    id: "22222222-2222-2222-2222-222222222222"
    path: forms/form1.form.yaml
"""
    manifest = parse_manifest(yaml_str)
    ids = get_all_entity_ids(manifest)
    assert "11111111-1111-1111-1111-111111111111" in ids
    assert "22222222-2222-2222-2222-222222222222" in ids


def test_get_paths():
    """Get all file paths from manifest."""
    from src.services.manifest import parse_manifest, get_all_paths

    yaml_str = """
workflows:
  wf1:
    id: "11111111-1111-1111-1111-111111111111"
    path: workflows/wf1.py
    function_name: wf1
    type: workflow
forms:
  form1:
    id: "22222222-2222-2222-2222-222222222222"
    path: forms/form1.form.yaml
"""
    manifest = parse_manifest(yaml_str)
    paths = get_all_paths(manifest)
    assert "workflows/wf1.py" in paths
    assert "forms/form1.form.yaml" in paths
