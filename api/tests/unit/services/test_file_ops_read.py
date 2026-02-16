"""Unit tests for read_file module cache guard."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


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
