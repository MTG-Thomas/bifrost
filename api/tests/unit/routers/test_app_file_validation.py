"""Unit tests for CSS file validation in app code files."""

import pytest
from fastapi import HTTPException

from src.routers.app_code_files import validate_file_path


def test_css_file_at_root_allowed():
    """styles.css is valid at app root."""
    validate_file_path("styles.css")  # Should not raise


def test_css_file_only_styles_allowed():
    """Only styles.css is allowed, not arbitrary CSS files."""
    with pytest.raises(HTTPException):
        validate_file_path("theme.css")


def test_css_in_subdirectory_rejected():
    """CSS files are only allowed at root, not in subdirectories."""
    with pytest.raises(HTTPException):
        validate_file_path("pages/styles.css")
