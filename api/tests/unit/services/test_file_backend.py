"""Unit tests for S3Backend path handling."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.file_backend import S3Backend


class TestS3BackendPathHandling:
    """Verify S3Backend passes correct path format to each downstream method."""

    def _make_backend(self):
        mock_db = MagicMock()
        with patch("src.services.file_backend.FileStorageService") as MockFSS:
            backend = S3Backend(mock_db)
            backend.storage = MockFSS.return_value
            return backend

    @pytest.mark.asyncio
    async def test_read_passes_workspace_relative_path(self):
        """read() should pass workspace-relative path, not _repo/ prefixed."""
        backend = self._make_backend()
        backend.storage.read_file = AsyncMock(return_value=(b"content", None))

        await backend.read("workflows/test.py", "workspace")

        backend.storage.read_file.assert_called_once_with("workflows/test.py")

    @pytest.mark.asyncio
    async def test_write_passes_workspace_relative_path(self):
        """write() should pass workspace-relative path, not _repo/ prefixed."""
        backend = self._make_backend()
        backend.storage.write_file = AsyncMock()

        await backend.write("workflows/test.py", b"content", "workspace", "user")

        backend.storage.write_file.assert_called_once_with(
            "workflows/test.py", b"content", "user"
        )

    @pytest.mark.asyncio
    async def test_delete_passes_workspace_relative_path(self):
        """delete() should pass workspace-relative path, not _repo/ prefixed."""
        backend = self._make_backend()
        backend.storage.delete_file = AsyncMock()

        await backend.delete("workflows/test.py", "workspace")

        backend.storage.delete_file.assert_called_once_with("workflows/test.py")

    @pytest.mark.asyncio
    async def test_list_passes_workspace_relative_directory(self):
        """list() should pass workspace-relative directory, not _repo/ prefixed."""
        backend = self._make_backend()
        backend.storage.list_files = AsyncMock(return_value=[])

        await backend.list("workflows", "workspace")

        backend.storage.list_files.assert_called_once_with("workflows")

    @pytest.mark.asyncio
    async def test_exists_passes_full_s3_key(self):
        """exists() should pass _repo/ prefixed path (file_exists expects S3 key)."""
        backend = self._make_backend()
        backend.storage.file_exists = AsyncMock(return_value=True)

        await backend.exists("workflows/test.py", "workspace")

        backend.storage.file_exists.assert_called_once_with("_repo/workflows/test.py")
