"""Unit tests for app serving object-storage behavior."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.app_storage import AppStorageService


class _NoSuchKey(Exception):
    pass


class _Body:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self) -> bytes:
        return self._data


class _FakeClient:
    exceptions = SimpleNamespace(NoSuchKey=_NoSuchKey)

    def __init__(self, pages=None):
        self.pages = list(pages or [])
        self.copy_calls = []
        self.delete_calls = []
        self.put_calls = []
        self.get_results = {}
        self.list_calls = []

    async def list_objects_v2(self, **kwargs):
        self.list_calls.append(kwargs)
        if self.pages:
            return self.pages.pop(0)
        return {"Contents": [], "IsTruncated": False}

    async def copy_object(self, **kwargs):
        self.copy_calls.append(kwargs)

    async def delete_object(self, **kwargs):
        self.delete_calls.append(kwargs)

    async def put_object(self, **kwargs):
        self.put_calls.append(kwargs)

    async def get_object(self, **kwargs):
        key = kwargs["Key"]
        result = self.get_results.get(key)
        if isinstance(result, Exception):
            raise result
        if result is None:
            raise _NoSuchKey(key)
        return {"Body": _Body(result)}


def _service(client: _FakeClient) -> AppStorageService:
    service = AppStorageService.__new__(AppStorageService)
    service._bucket = "bucket"

    @asynccontextmanager
    async def get_client():
        yield client

    service._storage = SimpleNamespace(get_client=get_client)
    return service


def test_key_normalizes_relative_paths():
    service = _service(_FakeClient())

    assert service._key("app-1", "preview") == "_apps/app-1/preview/"
    assert (
        service._key("app-1", "live", "/pages/index.tsx")
        == "_apps/app-1/live/pages/index.tsx"
    )


@pytest.mark.asyncio
async def test_list_keys_follows_continuation_and_skips_directory_markers():
    client = _FakeClient(
        pages=[
            {
                "Contents": [
                    {"Key": "_apps/app/preview/"},
                    {"Key": "_apps/app/preview/index.html"},
                ],
                "IsTruncated": True,
                "NextContinuationToken": "next",
            },
            {
                "Contents": [{"Key": "_apps/app/preview/app.js"}],
                "IsTruncated": False,
            },
        ]
    )
    service = _service(client)

    keys = await service._list_keys(client, "_apps/app/preview/")

    assert keys == ["_apps/app/preview/index.html", "_apps/app/preview/app.js"]
    assert client.list_calls == [
        {"Bucket": "bucket", "Prefix": "_apps/app/preview/"},
        {
            "Bucket": "bucket",
            "Prefix": "_apps/app/preview/",
            "ContinuationToken": "next",
        },
    ]


@pytest.mark.asyncio
async def test_sync_preview_copies_repo_files_deletes_stale_preview_and_invalidates_cache():
    client = _FakeClient(
        pages=[
            {
                "Contents": [
                    {"Key": "_repo/apps/portal/index.html"},
                    {"Key": "_repo/apps/portal/assets/app.js"},
                ],
                "IsTruncated": False,
            },
            {
                "Contents": [
                    {"Key": "_apps/app-1/preview/index.html"},
                    {"Key": "_apps/app-1/preview/stale.css"},
                ],
                "IsTruncated": False,
            },
        ]
    )
    service = _service(client)
    service.invalidate_render_cache = AsyncMock()

    synced = await service.sync_preview("app-1", "apps/portal")

    assert synced == 2
    assert client.copy_calls == [
        {
            "Bucket": "bucket",
            "CopySource": {"Bucket": "bucket", "Key": "_repo/apps/portal/index.html"},
            "Key": "_apps/app-1/preview/index.html",
        },
        {
            "Bucket": "bucket",
            "CopySource": {
                "Bucket": "bucket",
                "Key": "_repo/apps/portal/assets/app.js",
            },
            "Key": "_apps/app-1/preview/assets/app.js",
        },
    ]
    assert client.delete_calls == [
        {"Bucket": "bucket", "Key": "_apps/app-1/preview/stale.css"}
    ]
    service.invalidate_render_cache.assert_awaited_once_with("app-1")


@pytest.mark.asyncio
async def test_read_file_maps_storage_missing_variants_to_file_not_found():
    client = _FakeClient()
    service = _service(client)

    with pytest.raises(FileNotFoundError, match="missing.txt"):
        await service.read_file("app-1", "preview", "missing.txt")

    client.get_results["_apps/app-1/preview/missing-404.txt"] = RuntimeError("404")
    with pytest.raises(FileNotFoundError, match="missing-404.txt"):
        await service.read_file("app-1", "preview", "missing-404.txt")


@pytest.mark.asyncio
async def test_list_files_returns_relative_non_empty_paths():
    client = _FakeClient(
        pages=[
            {
                "Contents": [
                    {"Key": "_apps/app-1/live/"},
                    {"Key": "_apps/app-1/live/index.html"},
                    {"Key": "_apps/app-1/live/assets/app.js"},
                ],
                "IsTruncated": False,
            }
        ]
    )
    service = _service(client)

    assert await service.list_files("app-1", "live") == [
        "index.html",
        "assets/app.js",
    ]


@pytest.mark.asyncio
async def test_publish_returns_zero_when_preview_is_empty_and_keeps_cache():
    client = _FakeClient(pages=[{"Contents": [], "IsTruncated": False}])
    service = _service(client)
    service.invalidate_render_cache = AsyncMock()

    assert await service.publish("app-1") == 0

    assert client.copy_calls == []
    assert client.delete_calls == []
    service.invalidate_render_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_copies_preview_to_live_deletes_stale_live_and_invalidates_cache():
    client = _FakeClient(
        pages=[
            {
                "Contents": [
                    {"Key": "_apps/app-1/preview/index.html"},
                    {"Key": "_apps/app-1/preview/assets/app.js"},
                ],
                "IsTruncated": False,
            },
            {
                "Contents": [
                    {"Key": "_apps/app-1/live/index.html"},
                    {"Key": "_apps/app-1/live/old.js"},
                ],
                "IsTruncated": False,
            },
        ]
    )
    service = _service(client)
    service.invalidate_render_cache = AsyncMock()

    assert await service.publish("app-1") == 2

    copied = {call["Key"]: call["CopySource"]["Key"] for call in client.copy_calls}
    assert copied == {
        "_apps/app-1/live/index.html": "_apps/app-1/preview/index.html",
        "_apps/app-1/live/assets/app.js": "_apps/app-1/preview/assets/app.js",
    }
    assert client.delete_calls == [
        {"Bucket": "bucket", "Key": "_apps/app-1/live/old.js"}
    ]
    service.invalidate_render_cache.assert_awaited_once_with("app-1")


@pytest.mark.asyncio
async def test_render_cache_round_trip_and_best_effort_failures(monkeypatch):
    redis = SimpleNamespace(
        get=AsyncMock(return_value='{"index.html":"<main></main>"}'),
        set=AsyncMock(),
        delete=AsyncMock(),
    )
    monkeypatch.setattr("src.core.cache.get_shared_redis", AsyncMock(return_value=redis))
    service = _service(_FakeClient())

    assert await service.get_render_cache("app-1", "preview") == {
        "index.html": "<main></main>"
    }
    await service.set_render_cache("app-1", "live", {"app.js": "console.log(1)"})
    await service.invalidate_render_cache("app-1")

    redis.get.assert_awaited_once_with("bifrost:app_render:app-1:preview")
    redis.set.assert_awaited_once_with(
        "bifrost:app_render:app-1:live",
        '{"app.js": "console.log(1)"}',
    )
    redis.delete.assert_awaited_once_with(
        "bifrost:app_render:app-1:preview",
        "bifrost:app_render:app-1:live",
    )

    monkeypatch.setattr(
        "src.core.cache.get_shared_redis",
        AsyncMock(side_effect=RuntimeError("redis down")),
    )
    assert await service.get_render_cache("app-1", "preview") is None
    await service.set_render_cache("app-1", "live", {"app.js": "x"})
    await service.invalidate_render_cache("app-1")
