"""Regression tests for user-controlled values logged by file storage services."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.file_storage.deactivation import DeactivationProtectionService
from src.services.file_storage.service import FileStorageService


def _assert_single_line(message: str) -> None:
    assert "\n" not in message
    assert "\r" not in message
    assert "\\n" in message


@pytest.mark.asyncio
async def test_invalid_replacement_id_is_log_safe() -> None:
    service = DeactivationProtectionService(AsyncMock())

    with patch("src.services.file_storage.deactivation.logger.warning") as warning:
        await service.apply_workflow_replacements({"invalid\nforged": "replacement"})

    _assert_single_line(warning.call_args.args[0])


@pytest.mark.asyncio
async def test_replacement_function_name_is_log_safe() -> None:
    service = DeactivationProtectionService(AsyncMock())

    with patch("src.services.file_storage.deactivation.logger.info") as info:
        await service.apply_workflow_replacements(
            {str(uuid4()): "replacement\nforged"}
        )

    _assert_single_line(info.call_args.args[0])


@pytest.mark.asyncio
async def test_deactivated_file_path_is_log_safe() -> None:
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(rowcount=1)
    service = DeactivationProtectionService(db)

    with patch("src.services.file_storage.deactivation.logger.info") as info:
        await service.deactivate_removed_workflows("workflow\nforged.py", set())

    _assert_single_line(info.call_args.args[0])


@pytest.mark.asyncio
async def test_metadata_skip_path_is_log_safe() -> None:
    service = FileStorageService.__new__(FileStorageService)
    service._deactivation = MagicMock()
    service._deactivation.detect_pending_deactivations = AsyncMock(
        return_value=([], [])
    )

    with patch("src.services.file_storage.service.logger.info") as info:
        await service._extract_metadata_full(
            "workflow\nforged.py", b"", cached_content_str=""
        )

    _assert_single_line(info.call_args.args[0])


@pytest.mark.asyncio
async def test_syntax_error_path_and_detail_are_log_safe() -> None:
    service = FileStorageService.__new__(FileStorageService)

    with patch("src.services.file_storage.service.logger.warning") as warning:
        await service._index_python_file_full(
            "workflow\nforged.py", b"def broken(:\n"
        )

    message = warning.call_args.args[0]
    assert "\n" not in message
    assert "\r" not in message
    assert "\\n" in message
