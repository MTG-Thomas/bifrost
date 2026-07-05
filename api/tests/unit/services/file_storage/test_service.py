from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.file_storage import service as service_module
from src.services.file_storage.service import _SCOPE_SKIP, _scope_to_org_id


def test_scope_to_org_id_maps_workspace_and_global_to_unscoped_metadata() -> None:
    assert _scope_to_org_id("workspace", None) is None
    assert _scope_to_org_id("uploads", "global") is None


def test_scope_to_org_id_accepts_uuid_scope_for_non_workspace_locations() -> None:
    org_id = uuid4()

    assert _scope_to_org_id("uploads", str(org_id)) == org_id


@pytest.mark.parametrize("scope", [None, "not-a-uuid"])
def test_scope_to_org_id_skips_missing_or_invalid_non_workspace_scope(
    scope: str | None,
) -> None:
    assert _scope_to_org_id("uploads", scope) is _SCOPE_SKIP


@pytest.mark.asyncio
async def test_record_file_write_metadata_skips_invalid_scope_without_policy_write(
    monkeypatch,
) -> None:
    policy_instances: list[object] = []

    class Policy:
        def __init__(self, db):
            policy_instances.append(self)

        async def upsert_metadata(self, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("metadata should not be written")

    monkeypatch.setattr(
        "src.services.file_policy_service.FilePolicyService",
        Policy,
    )
    service = service_module.FileStorageService.__new__(service_module.FileStorageService)
    service.db = object()
    service._file_ops = SimpleNamespace(record_signed_upload_metadata=None)

    await service_module.FileStorageService.record_file_write_metadata(
        service,
        location="uploads",
        scope="not-a-uuid",
        path="uploads/file.txt",
        s3_path="uploads/file.txt",
        content_type="text/plain",
        size_bytes=12,
        sha256="abc",
        updated_by="user",
        user_id="user-1",
    )

    assert policy_instances == []
