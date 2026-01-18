"""Unit tests for code file MCP tools."""

import pytest

from src.services.mcp_server.tools.code_files import _validate_file_path


class TestValidateFilePath:
    """Tests for the _validate_file_path validation function."""

    # Valid paths
    def test_valid_root_layout(self):
        assert _validate_file_path("_layout") is None

    def test_valid_root_providers(self):
        assert _validate_file_path("_providers") is None

    def test_valid_pages(self):
        assert _validate_file_path("pages/index") is None
        assert _validate_file_path("pages/_layout") is None
        assert _validate_file_path("pages/clients/index") is None

    def test_valid_components(self):
        assert _validate_file_path("components/Button") is None
        assert _validate_file_path("components/ui/Card") is None

    def test_valid_modules(self):
        assert _validate_file_path("modules/api") is None
        assert _validate_file_path("modules/services/auth") is None

    def test_valid_dynamic_routes(self):
        assert _validate_file_path("pages/[id]") is None
        assert _validate_file_path("pages/clients/[id]/edit") is None

    # Invalid paths
    def test_invalid_empty(self):
        error = _validate_file_path("")
        assert error is not None
        assert "empty" in error.lower()

    def test_invalid_root_file(self):
        error = _validate_file_path("main")
        assert error is not None
        assert "_layout" in error or "_providers" in error

    def test_invalid_top_dir(self):
        error = _validate_file_path("services/api")
        assert error is not None
        assert "pages" in error

    def test_invalid_dynamic_in_components(self):
        error = _validate_file_path("components/[id]")
        assert error is not None
        assert "Dynamic" in error

    def test_invalid_layout_in_modules(self):
        error = _validate_file_path("modules/_layout")
        assert error is not None
        assert "_layout" in error

    def test_strips_slashes(self):
        assert _validate_file_path("/pages/index/") is None
        assert _validate_file_path("/_layout") is None
