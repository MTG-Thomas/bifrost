"""Tests for MCP tools _read_from_s3.

Verifies the function reads directly from S3 via RepoStorage,
handling success, missing files, and binary content.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_read_from_s3_returns_content():
    """When S3 has the file, return decoded UTF-8 content."""
    from src.services.mcp_server.tools.code_editor import _read_from_s3

    with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_cls:
        mock_repo = MagicMock()
        mock_repo.read = AsyncMock(return_value=b"def hello(): pass")
        mock_cls.return_value = mock_repo

        result = await _read_from_s3("workflows/test.py")

    assert result == "def hello(): pass"
    mock_repo.read.assert_called_once_with("workflows/test.py")


@pytest.mark.asyncio
async def test_read_from_s3_returns_none_when_not_found():
    """When S3 read raises an exception, return None."""
    from src.services.mcp_server.tools.code_editor import _read_from_s3

    with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_cls:
        mock_repo = MagicMock()
        mock_repo.read = AsyncMock(side_effect=FileNotFoundError("not found"))
        mock_cls.return_value = mock_repo

        result = await _read_from_s3("workflows/nonexistent.py")

    assert result is None


@pytest.mark.asyncio
async def test_read_from_s3_returns_none_for_binary():
    """When file content is binary (UnicodeDecodeError), return None."""
    from src.services.mcp_server.tools.code_editor import _read_from_s3

    with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_cls:
        mock_repo = MagicMock()
        mock_repo.read = AsyncMock(return_value=b"\x80\x81\x82\xff")
        mock_cls.return_value = mock_repo

        result = await _read_from_s3("files/image.png")

    assert result is None
