"""Unit tests for POST /api/files/assemble endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.routers.files import FileAssembleRequest, assemble_file


class TestAssembleFile:
    @pytest.mark.asyncio
    async def test_rejects_empty_chunk_list(self):
        req = FileAssembleRequest(path="modules/example.py", chunk_paths=[])
        ctx = MagicMock()
        user = MagicMock()
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await assemble_file(req, ctx, user, db)

        assert exc_info.value.status_code == 400
        assert "must not be empty" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    @patch("src.routers.files.get_backend")
    async def test_assembles_chunks_and_cleans_up(self, mock_get_backend):
        backend = MagicMock()
        backend.read = AsyncMock(side_effect=[b"hello ", b"world"])
        backend.write = AsyncMock()
        backend.delete = AsyncMock()
        mock_get_backend.return_value = backend

        req = FileAssembleRequest(
            path="modules/example.py",
            chunk_paths=["cli-chunks/upload/00000.part", "cli-chunks/upload/00001.part"],
        )
        ctx = MagicMock()
        user = MagicMock()
        user.email = "thomas@example.com"
        db = AsyncMock()

        await assemble_file(req, ctx, user, db)

        assert backend.read.await_count == 2
        backend.write.assert_awaited_once_with("modules/example.py", b"hello world", "workspace", "thomas@example.com")
        assert backend.delete.await_count == 2

    @pytest.mark.asyncio
    @patch("src.routers.files.get_backend")
    async def test_cleanup_still_runs_on_write_error(self, mock_get_backend):
        backend = MagicMock()
        backend.read = AsyncMock(return_value=b"chunk")
        backend.write = AsyncMock(side_effect=ValueError("bad path"))
        backend.delete = AsyncMock()
        mock_get_backend.return_value = backend

        req = FileAssembleRequest(path="../bad", chunk_paths=["cli-chunks/upload/00000.part"])
        ctx = MagicMock()
        user = MagicMock()
        user.email = "thomas@example.com"
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await assemble_file(req, ctx, user, db)

        assert exc_info.value.status_code == 400
        backend.delete.assert_awaited_once_with("cli-chunks/upload/00000.part", "temp")
