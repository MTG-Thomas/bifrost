"""Tests for dependency reading and validation in the render endpoint."""
import pytest
from src.routers.app_code_files import _parse_dependencies


def test_parse_valid_dependencies():
    """Valid dependencies are returned as dict."""
    yaml_content = """
name: Test App
dependencies:
  recharts: "2.12"
  dayjs: "1.11"
"""
    deps = _parse_dependencies(yaml_content)
    assert deps == {"recharts": "2.12", "dayjs": "1.11"}


def test_parse_no_dependencies():
    """YAML without dependencies returns empty dict."""
    yaml_content = """
name: Test App
description: No deps
"""
    deps = _parse_dependencies(yaml_content)
    assert deps == {}


def test_parse_empty_yaml():
    """Empty YAML returns empty dict."""
    deps = _parse_dependencies("")
    assert deps == {}


def test_parse_none_yaml():
    """None input returns empty dict."""
    deps = _parse_dependencies(None)
    assert deps == {}


def test_parse_invalid_yaml():
    """Malformed YAML returns empty dict (graceful degradation)."""
    deps = _parse_dependencies("{{{{not yaml")
    assert deps == {}


def test_parse_invalid_package_name_skipped():
    """Invalid package names are skipped."""
    yaml_content = """
dependencies:
  recharts: "2.12"
  "../malicious": "1.0"
  "good-pkg": "1.0"
"""
    deps = _parse_dependencies(yaml_content)
    assert "recharts" in deps
    assert "good-pkg" in deps
    assert "../malicious" not in deps


def test_parse_invalid_version_skipped():
    """Invalid version strings are skipped."""
    yaml_content = """
dependencies:
  recharts: "2.12"
  dayjs: "latest"
"""
    deps = _parse_dependencies(yaml_content)
    assert "recharts" in deps
    assert "dayjs" not in deps


def test_parse_max_20_dependencies():
    """More than 20 dependencies are truncated."""
    lines = ["dependencies:"]
    for i in range(25):
        lines.append(f"  pkg-{i}: \"{i}.0\"")
    yaml_content = "\n".join(lines)
    deps = _parse_dependencies(yaml_content)
    assert len(deps) == 20


def test_parse_scoped_package_name():
    """Scoped packages like @scope/pkg are valid."""
    yaml_content = """
dependencies:
  "@tanstack/react-table": "8.20"
"""
    deps = _parse_dependencies(yaml_content)
    assert "@tanstack/react-table" in deps


def test_parse_caret_tilde_versions():
    """Versions with ^ or ~ prefix are valid."""
    yaml_content = """
dependencies:
  recharts: "^2.12"
  dayjs: "~1.11.3"
"""
    deps = _parse_dependencies(yaml_content)
    assert deps == {"recharts": "^2.12", "dayjs": "~1.11.3"}
