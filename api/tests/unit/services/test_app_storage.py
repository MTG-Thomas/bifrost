from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.services.app_storage import AppStorageService


def _service(client=None) -> AppStorageService:
    service = AppStorageService.__new__(AppStorageService)
    service._bucket = "bucket"
    if client is not None:
        service._get_client = _client_context(client)  # type: ignore[method-assign]
    return service


def _client_context(client):
    @asynccontextmanager
    async def context():
        yield client

    return context


def test_key_builds_app_storage_prefixes() -> None:
    service = _service()

    assert service._key("app-1", "preview") == "_apps/app-1/preview/"
    assert service._key("app-1", "live", "/pages/index.tsx") == (
        "_apps/app-1/live/pages/index.tsx"
    )


def test_render_cache_key_includes_app_and_mode() -> None:
    assert AppStorageService._render_cache_key("app-1", "live") == (
        "bifrost:app_render:app-1:live"
    )


@pytest.mark.asyncio
async def test_list_keys_paginates_and_skips_directory_markers() -> None:
    client = _FakeClient(
        list_pages=[
            {
                "Contents": [
                    {"Key": "_apps/app/preview/"},
                    {"Key": "_apps/app/preview/a.tsx"},
                ],
                "IsTruncated": True,
                "NextContinuationToken": "next",
            },
            {
                "Contents": [{"Key": "_apps/app/preview/b.tsx"}],
                "IsTruncated": False,
            },
        ]
    )
    service = _service(client)

    keys = await service._list_keys(client, "_apps/app/preview/")

    assert keys == ["_apps/app/preview/a.tsx", "_apps/app/preview/b.tsx"]
    assert client.list_calls[1]["ContinuationToken"] == "next"


@pytest.mark.asyncio
async def test_read_file_returns_body_bytes() -> None:
    client = _FakeClient(objects={"_apps/app/preview/index.tsx": b"export default 1"})
    service = _service(client)

    assert await service.read_file("app", "preview", "index.tsx") == b"export default 1"


@pytest.mark.asyncio
async def test_read_file_maps_no_such_key_to_file_not_found() -> None:
    client = _FakeClient(objects={})
    service = _service(client)

    with pytest.raises(FileNotFoundError, match="missing.tsx"):
        await service.read_file("app", "preview", "missing.tsx")


@pytest.mark.asyncio
async def test_list_files_returns_relative_non_empty_paths() -> None:
    client = _FakeClient(
        list_pages=[
            {
                "Contents": [
                    {"Key": "_apps/app/live/"},
                    {"Key": "_apps/app/live/index.tsx"},
                    {"Key": "_apps/app/live/components/Button.tsx"},
                ],
                "IsTruncated": False,
            }
        ]
    )
    service = _service(client)

    assert await service.list_files("app", "live") == [
        "index.tsx",
        "components/Button.tsx",
    ]


@pytest.mark.asyncio
async def test_publish_returns_zero_without_preview_files() -> None:
    client = _FakeClient(list_pages=[{"Contents": [], "IsTruncated": False}])
    service = _service(client)

    assert await service.publish("app") == 0
    assert client.copied == []
    assert client.deleted == []


@pytest.mark.asyncio
async def test_publish_copies_preview_and_removes_stale_live(monkeypatch) -> None:
    client = _FakeClient(
        list_pages=[
            {
                "Contents": [
                    {"Key": "_apps/app/preview/index.tsx"},
                    {"Key": "_apps/app/preview/components/Button.tsx"},
                ],
                "IsTruncated": False,
            },
            {
                "Contents": [
                    {"Key": "_apps/app/live/index.tsx"},
                    {"Key": "_apps/app/live/old.tsx"},
                ],
                "IsTruncated": False,
            },
        ]
    )
    service = _service(client)
    invalidated: list[str] = []

    async def invalidate(app_id: str) -> None:
        invalidated.append(app_id)

    monkeypatch.setattr(service, "invalidate_render_cache", invalidate)

    assert await service.publish("app") == 2
    assert {copy["Key"] for copy in client.copied} == {
        "_apps/app/live/index.tsx",
        "_apps/app/live/components/Button.tsx",
    }
    assert client.deleted == [{"Bucket": "bucket", "Key": "_apps/app/live/old.tsx"}]
    assert invalidated == ["app"]


class _Body:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def read(self) -> bytes:
        return self.data


class _FakeClient:
    def __init__(
        self,
        *,
        list_pages: list[dict] | None = None,
        objects: dict[str, bytes] | None = None,
    ) -> None:
        self.list_pages = list(list_pages or [])
        self.objects = objects or {}
        self.list_calls: list[dict] = []
        self.copied: list[dict] = []
        self.deleted: list[dict] = []
        self.exceptions = SimpleNamespace(NoSuchKey=KeyError)

    async def list_objects_v2(self, **kwargs):
        self.list_calls.append(kwargs)
        return self.list_pages.pop(0)

    async def get_object(self, *, Bucket: str, Key: str):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": _Body(self.objects[Key])}

    async def copy_object(self, **kwargs):
        self.copied.append(kwargs)

    async def delete_object(self, **kwargs):
        self.deleted.append(kwargs)
