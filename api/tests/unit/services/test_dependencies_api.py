"""Tests for the dependencies API endpoint helper."""
from src.routers.app_code_files import _parse_dependencies, _serialize_dependencies


def test_serialize_empty_dependencies():
    """Empty deps produce valid YAML with empty dependencies."""
    result = _serialize_dependencies({}, existing_yaml=None)
    assert "dependencies:" not in result or "dependencies: {}" in result


def test_serialize_adds_dependencies_to_existing():
    """Adding deps preserves other app.yaml fields."""
    existing = "name: My App\ndescription: Cool app\n"
    result = _serialize_dependencies(
        {"recharts": "2.12", "dayjs": "1.11"}, existing_yaml=existing
    )
    assert "name: My App" in result
    assert "recharts" in result
    assert "dayjs" in result


def test_serialize_replaces_existing_dependencies():
    """Updating deps replaces the old dependencies section."""
    existing = "name: My App\ndependencies:\n  old-pkg: '1.0'\n"
    result = _serialize_dependencies({"new-pkg": "2.0"}, existing_yaml=existing)
    assert "new-pkg" in result
    assert "old-pkg" not in result


def test_serialize_creates_yaml_from_scratch():
    """When no existing YAML, creates a minimal app.yaml."""
    result = _serialize_dependencies({"recharts": "2.12"}, existing_yaml=None)
    assert "recharts" in result


def test_roundtrip_parse_serialize():
    """Serialized deps can be parsed back identically."""
    deps = {"recharts": "^2.12", "dayjs": "~1.11.3", "@tanstack/react-table": "8.20"}
    yaml_str = _serialize_dependencies(deps, existing_yaml="name: Test\n")
    parsed = _parse_dependencies(yaml_str)
    assert parsed == deps
