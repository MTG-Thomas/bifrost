"""Unit tests for pure FileOperationsService branches."""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


class _AsyncClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _S3ClientFactory:
    def __init__(self, client):
        self.client = client

    def get_client(self):
        return _AsyncClientContext(self.client)


def _service(**attrs):
    service = SimpleNamespace(settings=SimpleNamespace())
    for name, value in attrs.items():
        setattr(service, name, value)
    return service


class TestReadFileModuleCacheGuard:
    """Verify get_module() is only called for .py files."""

    @pytest.mark.asyncio
    async def test_read_py_file_checks_module_cache(self):
        """Python files should check Redis module cache."""
        with patch("src.core.module_cache.get_module", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"content": "print('hello')", "hash": "abc123"}

            from src.services.file_storage.file_ops import FileOperationsService

            service = MagicMock(spec=FileOperationsService)
            service.read_file = FileOperationsService.read_file.__get__(service)

            result = await service.read_file("workflows/test.py")

            mock_get.assert_called_once_with("workflows/test.py")
            assert result[0] == b"print('hello')"

    @pytest.mark.asyncio
    async def test_read_tsx_file_skips_module_cache(self):
        """Non-Python files should NOT check Redis module cache."""
        with patch("src.core.module_cache.get_module", new_callable=AsyncMock) as mock_get:
            # Set up S3 fallback
            mock_s3_client = AsyncMock()
            mock_response = {"Body": AsyncMock()}
            mock_response["Body"].read = AsyncMock(return_value=b"<div>hello</div>")
            mock_s3_client.get_object = AsyncMock(return_value=mock_response)

            from src.services.file_storage.file_ops import FileOperationsService

            service = MagicMock()
            service._s3_client.get_client.return_value.__aenter__ = AsyncMock(return_value=mock_s3_client)
            service._s3_client.get_client.return_value.__aexit__ = AsyncMock()
            service.settings.s3_bucket = "test"
            service.read_file = FileOperationsService.read_file.__get__(service)

            await service.read_file("apps/myapp/pages/index.tsx")

            mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_read_py_file_falls_back_to_s3_when_module_cache_empty(self):
        """Python files should still read durable S3 content on a cache miss."""
        with patch("src.core.module_cache.get_module", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            mock_s3_client = MagicMock()
            mock_response = {"Body": MagicMock()}
            mock_response["Body"].read = AsyncMock(return_value=b"from workflows import run")
            mock_s3_client.get_object = AsyncMock(return_value=mock_response)

            from src.services.file_storage.file_ops import FileOperationsService

            service = _service(_s3_client=_S3ClientFactory(mock_s3_client))
            service.settings.s3_bucket = "workspace-bucket"
            service.read_file = FileOperationsService.read_file.__get__(service)

            content, metadata = await service.read_file("workflows/cache_miss.py")

            mock_get.assert_called_once_with("workflows/cache_miss.py")
            mock_s3_client.get_object.assert_awaited_once_with(
                Bucket="workspace-bucket",
                Key="_repo/workflows/cache_miss.py",
            )
            assert content == b"from workflows import run"
            assert metadata is None

    @pytest.mark.asyncio
    async def test_read_file_translates_s3_no_such_key(self):
        """S3 NoSuchKey should surface as a path-specific FileNotFoundError."""
        class NoSuchKey(Exception):
            pass

        mock_s3_client = MagicMock()
        mock_s3_client.exceptions.NoSuchKey = NoSuchKey
        mock_s3_client.get_object = AsyncMock(side_effect=NoSuchKey("missing"))

        from src.services.file_storage.file_ops import FileOperationsService

        service = _service(_s3_client=_S3ClientFactory(mock_s3_client))
        service.settings.s3_bucket = "workspace-bucket"
        service.read_file = FileOperationsService.read_file.__get__(service)

        with pytest.raises(FileNotFoundError, match="File not found: docs/missing.md"):
            await service.read_file("docs/missing.md")

        mock_s3_client.get_object.assert_awaited_once_with(
            Bucket="workspace-bucket",
            Key="_repo/docs/missing.md",
        )


class TestWriteFilePathValidation:
    """Verify write_file exits before storage for excluded paths."""

    @pytest.mark.asyncio
    async def test_write_file_rejects_excluded_path_before_hashing_or_storage(self):
        from src.services.file_storage.file_ops import FileOperationsService

        service = _service(
            _compute_hash=MagicMock(side_effect=AssertionError("hash should not run")),
            _s3_client=MagicMock(),
        )
        service.write_file = FileOperationsService.write_file.__get__(service)

        with patch("src.services.editor.file_filter.is_excluded_path", return_value=True):
            with pytest.raises(ValueError, match="Path is excluded from workspace: __pycache__/x.pyc"):
                await service.write_file("__pycache__/x.pyc", b"compiled")


class TestDeleteFilePureBranches:
    """Verify delete side effects are best-effort and path-aware."""

    @pytest.mark.asyncio
    async def test_delete_file_continues_when_side_effect_fails(self):
        from src.services.file_storage.file_ops import FileOperationsService

        service = _service()
        service._delete_from_s3 = AsyncMock()
        service._remove_from_search_index = AsyncMock(side_effect=RuntimeError("index down"))
        service._handle_app_file_cleanup = AsyncMock()
        service._remove_metadata = AsyncMock()
        service._invalidate_module_cache_if_python = AsyncMock()
        service.delete_file = FileOperationsService.delete_file.__get__(service)

        with patch("src.core.pubsub.publish_file_activity", new_callable=AsyncMock) as publish:
            with patch("src.core.request_context.get_request_user", return_value=None):
                with patch("src.core.request_context.get_request_session_id", return_value="session-1"):
                    await service.delete_file("workflows/remove.py")

        service._delete_from_s3.assert_awaited_once_with("workflows/remove.py")
        service._remove_from_search_index.assert_awaited_once_with("workflows/remove.py")
        service._handle_app_file_cleanup.assert_awaited_once_with("workflows/remove.py")
        service._remove_metadata.assert_awaited_once_with("workflows/remove.py")
        service._invalidate_module_cache_if_python.assert_awaited_once_with("workflows/remove.py")
        publish.assert_awaited_once_with(
            user_id="system",
            user_name="system",
            activity_type="file_delete",
            paths=["workflows/remove.py"],
            session_id="session-1",
        )

    @pytest.mark.asyncio
    async def test_delete_from_s3_uses_repo_prefix(self):
        from src.services.file_storage.file_ops import FileOperationsService

        mock_s3_client = MagicMock()
        mock_s3_client.delete_object = AsyncMock()
        service = _service(_s3_client=_S3ClientFactory(mock_s3_client))
        service.settings.s3_bucket = "workspace-bucket"
        service._delete_from_s3 = FileOperationsService._delete_from_s3.__get__(service)

        await service._delete_from_s3("docs/readme.md")

        mock_s3_client.delete_object.assert_awaited_once_with(
            Bucket="workspace-bucket",
            Key="_repo/docs/readme.md",
        )

    @pytest.mark.asyncio
    async def test_invalidate_module_cache_for_detected_module_without_py_suffix(self):
        from src.services.file_storage.file_ops import FileOperationsService

        service = _service()
        service._invalidate_module_cache_if_python = (
            FileOperationsService._invalidate_module_cache_if_python.__get__(service)
        )

        with patch(
            "src.services.file_storage.file_ops.detect_platform_entity_type",
            return_value="module",
        ) as detect:
            with patch("src.services.file_storage.file_ops.invalidate_module", new_callable=AsyncMock) as invalidate:
                await service._invalidate_module_cache_if_python("modules/no_suffix")

        detect.assert_called_once_with("modules/no_suffix", b"")
        invalidate.assert_awaited_once_with("modules/no_suffix")

    @pytest.mark.asyncio
    async def test_handle_app_file_cleanup_returns_when_no_app_matches(self):
        from src.services.file_storage.file_ops import FileOperationsService

        service = _service()
        service._find_app_by_path = AsyncMock(return_value=None)
        service._handle_app_file_cleanup = FileOperationsService._handle_app_file_cleanup.__get__(service)

        await service._handle_app_file_cleanup("apps/missing/page.tsx")

        service._find_app_by_path.assert_awaited_once_with("apps/missing/page.tsx")


class TestMoveFilePreconditions:
    """Verify move_file path errors before storage mutations."""

    @pytest.mark.asyncio
    async def test_move_file_raises_when_old_path_missing(self):
        from src.services.file_storage.file_ops import FileOperationsService

        missing_result = MagicMock()
        missing_result.scalar_one_or_none.return_value = None
        db = MagicMock()
        db.execute = AsyncMock(return_value=missing_result)
        service = _service(db=db, _s3_client=MagicMock())
        service.move_file = FileOperationsService.move_file.__get__(service)

        with pytest.raises(FileNotFoundError, match="File not found: old.py"):
            await service.move_file("old.py", "new.py")

        db.execute.assert_awaited_once()
        service._s3_client.get_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_move_file_raises_when_new_path_exists(self):
        from src.services.file_storage.file_ops import FileOperationsService

        old_record = MagicMock()
        old_result = MagicMock()
        old_result.scalar_one_or_none.return_value = old_record
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = MagicMock()
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[old_result, existing_result])
        service = _service(db=db, _s3_client=MagicMock())
        service.move_file = FileOperationsService.move_file.__get__(service)

        with pytest.raises(FileExistsError, match="File already exists: new.py"):
            await service.move_file("old.py", "new.py")

        assert db.execute.await_count == 2
        service._s3_client.get_client.assert_not_called()
