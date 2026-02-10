"""Tests for MCP tools file_index fallback."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_file_index_fallback_returns_content():
    """When file_index has the content, fallback should return it."""
    from src.services.mcp_server.tools.code_editor import _try_file_index_fallback

    mock_row = MagicMock()
    mock_row.content = "def hello(): pass"
    mock_row.content_hash = "abc123"

    mock_result = MagicMock()
    mock_result.one_or_none.return_value = mock_row

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("src.services.mcp_server.tools.code_editor.get_db_context") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        content, metadata, error = await _try_file_index_fallback("workflows/test.py")

    assert content == "def hello(): pass"
    assert metadata is not None
    assert metadata["source"] == "file_index"
    assert error is None


@pytest.mark.asyncio
async def test_file_index_fallback_returns_none_when_not_found():
    """When file_index has no content, fallback should return None."""
    from src.services.mcp_server.tools.code_editor import _try_file_index_fallback

    mock_result = MagicMock()
    mock_result.one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("src.services.mcp_server.tools.code_editor.get_db_context") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        content, metadata, error = await _try_file_index_fallback("workflows/nonexistent.py")

    assert content is None
    assert metadata is None


@pytest.mark.asyncio
async def test_file_index_fallback_handles_missing_table():
    """If file_index table doesn't exist yet, fallback should not crash."""
    from src.services.mcp_server.tools.code_editor import _try_file_index_fallback

    with patch("src.services.mcp_server.tools.code_editor.get_db_context") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(
            side_effect=Exception("relation file_index does not exist")
        )
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        content, metadata, error = await _try_file_index_fallback("workflows/test.py")

    assert content is None
    assert metadata is None
