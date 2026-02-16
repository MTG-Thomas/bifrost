"""Tests for MCP tools _read_from_cache_or_s3.

Now that _read_from_cache_or_s3 delegates to get_module(), these tests
verify the thin wrapper behavior rather than the full Redis→S3 chain
(which is tested in test_module_cache.py).
"""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_cache_or_s3_returns_content_when_found():
    """When get_module returns a cached module, extract content."""
    from src.services.mcp_server.tools.code_editor import _read_from_cache_or_s3

    with patch(
        "src.core.module_cache.get_module",
        new_callable=AsyncMock,
        return_value={"content": "def hello(): pass", "path": "workflows/test.py", "hash": "abc"},
    ):
        result = await _read_from_cache_or_s3("workflows/test.py")

    assert result == "def hello(): pass"


@pytest.mark.asyncio
async def test_cache_or_s3_returns_none_when_not_found():
    """When get_module returns None, return None."""
    from src.services.mcp_server.tools.code_editor import _read_from_cache_or_s3

    with patch(
        "src.core.module_cache.get_module",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await _read_from_cache_or_s3("workflows/nonexistent.py")

    assert result is None
