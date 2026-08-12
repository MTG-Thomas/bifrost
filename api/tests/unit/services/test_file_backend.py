"""Unit tests for S3Backend path handling."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.file_backend import LocalBackend, S3Backend


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
    async def test_read_rejects_workspace_parent_traversal(self):
        backend = self._make_backend()
        backend.storage.read_file = AsyncMock(return_value=(b"content", None))

        with pytest.raises(ValueError, match="path traversal"):
            await backend.read("../secret.txt", "workspace")

        backend.storage.read_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_write_passes_workspace_relative_path(self):
        """write() should pass workspace-relative path, not _repo/ prefixed."""
        backend = self._make_backend()
        backend.storage.write_file = AsyncMock()

        await backend.write("workflows/test.py", b"content", "workspace", "user")

        backend.storage.write_file.assert_called_once_with(
            "workflows/test.py", b"content", "user", skip_dirty_flag=True
        )

    @pytest.mark.asyncio
    async def test_write_rejects_absolute_workspace_path(self):
        backend = self._make_backend()
        backend.storage.write_file = AsyncMock()

        with pytest.raises(ValueError, match="must be relative"):
            await backend.write("/etc/passwd", b"content", "workspace", "user")

        backend.storage.write_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_passes_workspace_relative_path(self):
        """delete() should pass workspace-relative path, not _repo/ prefixed."""
        backend = self._make_backend()
        backend.storage.delete_file = AsyncMock()

        await backend.delete("workflows/test.py", "workspace")

        backend.storage.delete_file.assert_called_once_with(
            "workflows/test.py", skip_dirty_flag=True
        )

    @pytest.mark.asyncio
    async def test_delete_rejects_workspace_parent_traversal(self):
        backend = self._make_backend()
        backend.storage.delete_file = AsyncMock()

        with pytest.raises(ValueError, match="path traversal"):
            await backend.delete("workflows/../../secret.txt", "workspace")

        backend.storage.delete_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_passes_workspace_relative_directory(self):
        """list() should pass workspace-relative directory, not _repo/ prefixed."""
        backend = self._make_backend()
        backend.storage.list_files = AsyncMock(return_value=[])

        await backend.list("workflows", "workspace")

        backend.storage.list_files.assert_called_once_with("workflows")

    @pytest.mark.asyncio
    async def test_list_rejects_workspace_parent_traversal(self):
        backend = self._make_backend()
        backend.storage.list_files = AsyncMock(return_value=[])

        with pytest.raises(ValueError, match="path traversal"):
            await backend.list("../", "workspace")

        backend.storage.list_files.assert_not_called()

    @pytest.mark.asyncio
    async def test_exists_passes_full_s3_key(self):
        """exists() should pass _repo/ prefixed path (file_exists expects S3 key)."""
        backend = self._make_backend()
        backend.storage.file_exists = AsyncMock(return_value=True)

        await backend.exists("workflows/test.py", "workspace")

        backend.storage.file_exists.assert_called_once_with("_repo/workflows/test.py")

    @pytest.mark.asyncio
    async def test_exists_rejects_absolute_workspace_path(self):
        backend = self._make_backend()
        backend.storage.file_exists = AsyncMock(return_value=True)

        with pytest.raises(ValueError, match="must be relative"):
            await backend.exists("/etc/passwd", "workspace")

        backend.storage.file_exists.assert_not_called()

    @pytest.mark.asyncio
    async def test_read_non_workspace_uses_resolved_s3_path(self):
        backend = self._make_backend()
        backend.storage.read_uploaded_file = AsyncMock(return_value=b"uploaded")

        assert await backend.read("docs/readme.txt", "uploads", "global") == b"uploaded"

        backend.storage.read_uploaded_file.assert_awaited_once_with(
            "uploads/global/docs/readme.txt"
        )

    @pytest.mark.asyncio
    async def test_read_non_workspace_maps_not_found_error(self):
        backend = self._make_backend()
        backend.storage.read_uploaded_file = AsyncMock(
            side_effect=RuntimeError("NoSuchKey")
        )

        with pytest.raises(FileNotFoundError, match="missing.txt"):
            await backend.read("missing.txt", "uploads", "global")

    @pytest.mark.asyncio
    async def test_read_non_workspace_maps_invalid_error(self):
        backend = self._make_backend()
        backend.storage.read_uploaded_file = AsyncMock(
            side_effect=RuntimeError("bad components in key")
        )

        with pytest.raises(ValueError, match="Invalid path: bad.txt"):
            await backend.read("bad.txt", "uploads", "global")

    @pytest.mark.asyncio
    async def test_write_delete_list_and_exists_non_workspace_use_resolved_paths(self):
        backend = self._make_backend()
        backend.storage.write_raw_to_s3 = AsyncMock()
        backend.storage.delete_raw_from_s3 = AsyncMock()
        backend.storage.list_raw_s3 = AsyncMock(
            return_value=[
                "reports/global/q1/a.txt",
                "reports/global/q1/nested/b.txt",
            ]
        )
        backend.storage.file_exists = AsyncMock(return_value=False)

        await backend.write("q1/a.txt", b"a", "reports", scope="global")
        await backend.delete("q1/a.txt", "reports", scope="global")
        listed = await backend.list("q1", "reports", scope="global")
        exists = await backend.exists("q1/a.txt", "reports", scope="global")

        backend.storage.write_raw_to_s3.assert_awaited_once_with(
            "reports/global/q1/a.txt", b"a"
        )
        backend.storage.delete_raw_from_s3.assert_awaited_once_with(
            "reports/global/q1/a.txt"
        )
        backend.storage.list_raw_s3.assert_awaited_once_with("reports/global/q1/")
        assert listed == ["q1/a.txt", "q1/nested/b.txt"]
        backend.storage.file_exists.assert_awaited_once_with("reports/global/q1/a.txt")
        assert exists is False


class TestLocalBackendSandbox:
    """Verify LocalBackend._resolve_path enforces a real containment check.

    Uses relative_to() rather than str.startswith() so a sibling-prefix path
    (base "/tmp/foo" vs "/tmp/foo_evil/x") cannot bypass the sandbox.
    """

    def _make_backend(self, tmp_path: Path) -> LocalBackend:
        """Build a LocalBackend rooted entirely under tmp_path."""
        backend = LocalBackend.__new__(LocalBackend)
        backend.workspace_root = tmp_path / "workspace"
        backend.temp_root = tmp_path / "temp"
        backend.uploads_root = tmp_path / "uploads"
        for d in (backend.workspace_root, backend.temp_root, backend.uploads_root):
            d.mkdir(parents=True, exist_ok=True)
        return backend

    def test_path_inside_sandbox_resolves(self, tmp_path: Path):
        backend = self._make_backend(tmp_path)

        resolved = backend._resolve_path("workflows/test.py", "workspace")

        assert resolved == (backend.workspace_root / "workflows/test.py").resolve()

    def test_parent_traversal_is_rejected(self, tmp_path: Path):
        backend = self._make_backend(tmp_path)

        with pytest.raises(ValueError, match="path traversal"):
            backend._resolve_path("../escape.txt", "workspace")

    def test_backslash_parent_traversal_is_rejected(self, tmp_path: Path):
        backend = self._make_backend(tmp_path)

        with pytest.raises(ValueError, match="path traversal"):
            backend._resolve_path(r"..\escape.txt", "workspace")

    def test_absolute_path_is_rejected_before_filesystem_access(self, tmp_path: Path):
        backend = self._make_backend(tmp_path)

        with pytest.raises(ValueError, match="must be relative"):
            backend._resolve_path(str(tmp_path / "temp_evil" / "x"), "temp")

    def test_location_parent_traversal_is_rejected(self, tmp_path: Path):
        backend = self._make_backend(tmp_path)

        with pytest.raises(ValueError, match="Invalid location name"):
            backend._resolve_path("safe.txt", "../outside")

    def test_safe_freeform_location_resolves_as_workspace_sibling(self, tmp_path: Path):
        backend = self._make_backend(tmp_path)

        resolved = backend._resolve_path("q1.txt", "reports")

        assert resolved == (backend.workspace_root.parent / "reports/q1.txt").resolve()

    @pytest.mark.asyncio
    async def test_write_read_list_exists_and_delete_round_trip(self, tmp_path: Path):
        backend = self._make_backend(tmp_path)

        await backend.write("nested/report.txt", b"hello", "workspace")

        assert await backend.read("nested/report.txt", "workspace") == b"hello"
        assert await backend.exists("nested/report.txt", "workspace") is True
        assert await backend.list("nested", "workspace") == ["nested/report.txt"]

        await backend.delete("nested/report.txt", "workspace")

        assert await backend.exists("nested/report.txt", "workspace") is False

    @pytest.mark.asyncio
    async def test_read_missing_file_raises_file_not_found(self, tmp_path: Path):
        backend = self._make_backend(tmp_path)

        with pytest.raises(FileNotFoundError, match="missing.txt"):
            await backend.read("missing.txt", "workspace")

    @pytest.mark.asyncio
    async def test_list_missing_directory_returns_empty_list(self, tmp_path: Path):
        backend = self._make_backend(tmp_path)

        assert await backend.list("missing", "workspace") == []


def test_get_backend_requires_db_for_cloud_mode() -> None:
    from src.services.file_backend import get_backend

    with pytest.raises(ValueError, match="Database session required"):
        get_backend("cloud")


def test_get_backend_returns_local_backend_for_local_mode() -> None:
    from src.services.file_backend import get_backend

    assert isinstance(get_backend("local"), LocalBackend)
