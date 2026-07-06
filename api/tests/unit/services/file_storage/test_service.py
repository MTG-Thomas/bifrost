from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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


@pytest.mark.asyncio
async def test_record_signed_upload_metadata_records_workspace_marker_and_policy_metadata(
    monkeypatch,
) -> None:
    policy_calls = []
    signed_upload_calls = []

    class Policy:
        def __init__(self, db):
            self.db = db

        async def upsert_metadata(self, **kwargs):
            policy_calls.append(kwargs)

    async def record_signed_upload_metadata(path, *, updated_by):
        signed_upload_calls.append((path, updated_by))

    monkeypatch.setattr("src.services.file_policy_service.FilePolicyService", Policy)
    service = service_module.FileStorageService.__new__(service_module.FileStorageService)
    service.db = object()
    service._file_ops = SimpleNamespace(
        record_signed_upload_metadata=record_signed_upload_metadata
    )

    await service_module.FileStorageService.record_signed_upload_metadata(
        service,
        location="workspace",
        scope="global",
        path="workflows/hello.py",
        s3_path="_repo/workflows/hello.py",
        content_type="text/x-python",
        size_bytes=100,
        sha256="abc",
        updated_by="alice",
        user_id="user-1",
    )

    assert signed_upload_calls == [("workflows/hello.py", "alice")]
    assert policy_calls == [
        {
            "organization_id": None,
            "location": "workspace",
            "path": "workflows/hello.py",
            "content_type": "text/x-python",
            "s3_key": "_repo/workflows/hello.py",
            "size_bytes": 100,
            "sha256": "abc",
            "updated_by": "user-1",
            "created_by": "user-1",
            "solution_id": None,
        }
    ]


@pytest.mark.asyncio
async def test_record_file_write_metadata_uses_solution_org_not_scope(monkeypatch) -> None:
    policy_calls = []
    solution_id = uuid4()
    org_id = uuid4()

    class Policy:
        def __init__(self, db):
            self.db = db

        async def upsert_metadata(self, **kwargs):
            policy_calls.append(kwargs)

    monkeypatch.setattr("src.services.file_policy_service.FilePolicyService", Policy)
    service = service_module.FileStorageService.__new__(service_module.FileStorageService)
    service.db = object()

    await service_module.FileStorageService.record_file_write_metadata(
        service,
        location="solutions",
        scope=str(solution_id),
        path="solution/files/readme.md",
        s3_path="solutions/install/readme.md",
        content_type="text/markdown",
        size_bytes=7,
        sha256="def",
        updated_by="alice",
        user_id="user-1",
        solution_id=solution_id,
        org_id=org_id,
    )

    assert policy_calls[0]["organization_id"] == org_id
    assert policy_calls[0]["solution_id"] == solution_id
    assert policy_calls[0]["created_by"] == "user-1"
    assert policy_calls[0]["updated_by"] == "user-1"


@pytest.mark.asyncio
async def test_raw_s3_helpers_use_storage_client_and_swallow_delete_failures() -> None:
    put_calls = []
    delete_calls = []

    class S3:
        async def put_object(self, **kwargs):
            put_calls.append(kwargs)

        async def delete_object(self, **kwargs):
            delete_calls.append(kwargs)
            raise RuntimeError("already gone")

    @asynccontextmanager
    async def get_client():
        yield S3()

    service = service_module.FileStorageService.__new__(service_module.FileStorageService)
    service.settings = SimpleNamespace(s3_bucket="files")
    service._s3_storage = SimpleNamespace(get_client=get_client)

    await service_module.FileStorageService.write_raw_to_s3(
        service,
        "uploads/report.csv",
        b"a,b",
    )
    await service_module.FileStorageService.delete_raw_from_s3(
        service,
        "uploads/missing.csv",
    )

    assert put_calls == [
        {
            "Bucket": "files",
            "Key": "uploads/report.csv",
            "Body": b"a,b",
            "ContentType": "text/csv",
        }
    ]
    assert delete_calls == [{"Bucket": "files", "Key": "uploads/missing.csv"}]


@pytest.mark.asyncio
async def test_list_raw_s3_collects_keys_from_paginated_results() -> None:
    class Paginator:
        async def paginate(self, **kwargs):
            yield {"Contents": [{"Key": "uploads/a.txt"}, {"Key": ""}]}
            yield {"Contents": [{"Key": "uploads/b.txt"}, {}]}

    class S3:
        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return Paginator()

    @asynccontextmanager
    async def get_client():
        yield S3()

    service = service_module.FileStorageService.__new__(service_module.FileStorageService)
    service.settings = SimpleNamespace(s3_bucket="files")
    service._s3_storage = SimpleNamespace(get_client=get_client)

    keys = await service_module.FileStorageService.list_raw_s3(service, "uploads/")

    assert keys == ["uploads/a.txt", "uploads/b.txt"]


@pytest.mark.asyncio
async def test_file_exists_handles_found_missing_and_unexpected_errors() -> None:
    class NoSuchKey(Exception):
        pass

    class S3:
        exceptions = SimpleNamespace(NoSuchKey=NoSuchKey)

        def __init__(self, outcome):
            self.outcome = outcome

        async def head_object(self, **kwargs):
            if self.outcome == "missing":
                raise NoSuchKey()
            if self.outcome == "error":
                raise RuntimeError("boom")

    service = service_module.FileStorageService.__new__(service_module.FileStorageService)
    service.settings = SimpleNamespace(s3_bucket="files")

    def set_client(outcome):
        @asynccontextmanager
        async def get_client():
            yield S3(outcome)

        service._s3_storage = SimpleNamespace(get_client=get_client)

    set_client("found")
    assert (
        await service_module.FileStorageService.file_exists(service, "uploads/a.txt")
        is True
    )

    set_client("missing")
    assert (
        await service_module.FileStorageService.file_exists(service, "uploads/a.txt")
        is False
    )

    set_client("error")
    assert (
        await service_module.FileStorageService.file_exists(service, "uploads/a.txt")
        is False
    )


@pytest.mark.asyncio
async def test_write_raw_chunks_delegates_to_storage_client() -> None:
    calls = []

    async def chunks() -> AsyncIterator[bytes]:
        yield b"hello"

    async def put_object_from_chunks(path, chunk_iter, *, content_type=None):
        calls.append((path, [chunk async for chunk in chunk_iter], content_type))
        return "sha", 5

    service = service_module.FileStorageService.__new__(service_module.FileStorageService)
    service._s3_storage = SimpleNamespace(put_object_from_chunks=put_object_from_chunks)

    result = await service_module.FileStorageService.write_raw_chunks_to_s3(
        service,
        "uploads/a.txt",
        chunks(),
        content_type="text/plain",
    )

    assert result == ("sha", 5)
    assert calls == [("uploads/a.txt", [b"hello"], "text/plain")]
