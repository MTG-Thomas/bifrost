from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.file_structure_service import FileStructureService
from src.services.file_structure_service import _scope_seg


def test_scope_seg_uses_global_for_none_and_uuid_string_for_org() -> None:
    org_id = uuid4()

    assert _scope_seg(None) == "global"
    assert _scope_seg(org_id) == str(org_id)


def _service():
    with (
        patch("src.services.file_structure_service.FileStorageService") as storage_cls,
        patch("src.services.file_structure_service.FilePolicyService") as policy_cls,
    ):
        service = FileStructureService(MagicMock())
        service.storage = storage_cls.return_value
        service.policies = policy_cls.return_value
        return service


@pytest.mark.asyncio
async def test_list_prefix_returns_sorted_direct_folders_before_files() -> None:
    service = _service()
    service.storage.list_raw_s3 = AsyncMock(
        return_value=[
            "reports/global/q1/summary.pdf",
            "reports/global/readme.md",
            "reports/global/q1/data.csv",
            "reports/global/z-last.txt",
        ]
    )

    entries = await service.list_prefix(org_id=None, location="reports", prefix="")

    assert [(entry.kind, entry.name, entry.path) for entry in entries] == [
        ("folder", "q1", "q1"),
        ("file", "readme.md", "readme.md"),
        ("file", "z-last.txt", "z-last.txt"),
    ]
    service.storage.list_raw_s3.assert_awaited_once_with("reports/global/")


@pytest.mark.asyncio
async def test_list_prefix_nested_prefix_preserves_relative_paths() -> None:
    service = _service()
    org_id = uuid4()
    service.storage.list_raw_s3 = AsyncMock(
        return_value=[
            f"reports/{org_id}/q1/january/summary.pdf",
            f"reports/{org_id}/q1/readme.md",
        ]
    )

    entries = await service.list_prefix(org_id=org_id, location="reports", prefix="q1")

    assert [(entry.kind, entry.name, entry.path) for entry in entries] == [
        ("folder", "january", "q1/january"),
        ("file", "readme.md", "q1/readme.md"),
    ]
    service.storage.list_raw_s3.assert_awaited_once_with(f"reports/{org_id}/q1/")


@pytest.mark.asyncio
async def test_list_prefix_ignores_base_key_and_deduplicates_folder_entries() -> None:
    service = _service()
    service.storage.list_raw_s3 = AsyncMock(
        return_value=[
            "reports/global/q1/",
            "reports/global/q1/january.csv",
            "reports/global/q1/february.csv",
            "reports/global/readme.md",
        ]
    )

    entries = await service.list_prefix(org_id=None, location="reports", prefix="")

    assert [(entry.kind, entry.name, entry.path) for entry in entries] == [
        ("folder", "q1", "q1"),
        ("file", "readme.md", "readme.md"),
    ]


@pytest.mark.asyncio
async def test_list_shares_merges_visible_file_and_policy_locations() -> None:
    service = _service()
    org_id = uuid4()
    service.storage.list_raw_s3 = AsyncMock(
        return_value=[
            f"reports/{org_id}/q1.pdf",
            f"uploads/{org_id}/invoice.pdf",
            f"_repo/{org_id}/hidden.py",
            "reports/other-org/ignore.pdf",
            "loose-file",
        ]
    )
    service.policies.list_policies = AsyncMock(
        return_value=[
            SimpleNamespace(location="reports"),
            SimpleNamespace(location="contracts"),
            SimpleNamespace(location="workspace"),
            SimpleNamespace(location="temp"),
        ]
    )

    shares = await service.list_shares(org_id=org_id)

    assert [
        (share.location, share.read_only, share.has_policy) for share in shares
    ] == [
        ("contracts", False, True),
        ("reports", False, True),
        ("uploads", True, False),
    ]
    service.storage.list_raw_s3.assert_awaited_once_with("")
    service.policies.list_policies.assert_awaited_once_with(organization_id=org_id)


@pytest.mark.asyncio
async def test_list_shares_for_global_scope_hides_reserved_prefixes_and_marks_uploads() -> None:
    service = _service()
    service.storage.list_raw_s3 = AsyncMock(
        return_value=[
            "uploads/global/manual.pdf",
            "reports/global/q1.pdf",
            "_tmp/global/hidden.txt",
            "_apps/global/hidden.js",
            "reports/org-specific/ignored.pdf",
        ]
    )
    service.policies.list_policies = AsyncMock(
        return_value=[
            SimpleNamespace(location="uploads"),
            SimpleNamespace(location="empty-share"),
        ]
    )

    shares = await service.list_shares(org_id=None)

    assert [
        (share.location, share.read_only, share.has_policy) for share in shares
    ] == [
        ("empty-share", False, True),
        ("reports", False, False),
        ("uploads", True, True),
    ]
