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
async def test_read_file_maps_404_error_to_file_not_found() -> None:
    client = _FakeClient(read_error=RuntimeError("404 Not Found"))
    service = _service(client)

    with pytest.raises(FileNotFoundError, match=r"missing\.tsx"):
        await service.read_file("app", "live", "missing.tsx")


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
async def test_write_preview_file_stores_content_and_invalidates_cache(
    monkeypatch,
) -> None:
    client = _FakeClient()
    service = _service(client)
    invalidated: list[str] = []

    async def invalidate(app_id: str) -> None:
        invalidated.append(app_id)

    monkeypatch.setattr(service, "invalidate_render_cache", invalidate)

    await service.write_preview_file("app", "/pages/index.tsx", b"hello")

    assert client.puts == [
        {
            "Bucket": "bucket",
            "Key": "_apps/app/preview/pages/index.tsx",
            "Body": b"hello",
        }
    ]
    assert invalidated == ["app"]


@pytest.mark.asyncio
async def test_delete_preview_file_ignores_storage_error_and_invalidates_cache(
    monkeypatch,
) -> None:
    client = _FakeClient(delete_error=RuntimeError("already gone"))
    service = _service(client)
    invalidated: list[str] = []

    async def invalidate(app_id: str) -> None:
        invalidated.append(app_id)

    monkeypatch.setattr(service, "invalidate_render_cache", invalidate)

    await service.delete_preview_file("app", "missing.tsx")

    assert client.deleted == [
        {"Bucket": "bucket", "Key": "_apps/app/preview/missing.tsx"}
    ]
    assert invalidated == ["app"]


@pytest.mark.asyncio
async def test_sync_preview_copies_repo_files_and_removes_stale_preview(
    monkeypatch,
) -> None:
    client = _FakeClient(
        list_pages=[
            {
                "Contents": [
                    {"Key": "_repo/apps/demo/index.tsx"},
                    {"Key": "_repo/apps/demo/components/Button.tsx"},
                ],
                "IsTruncated": False,
            },
            {
                "Contents": [
                    {"Key": "_apps/app/preview/index.tsx"},
                    {"Key": "_apps/app/preview/old.tsx"},
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

    assert await service.sync_preview("app", "apps/demo/") == 2
    assert {copy["Key"] for copy in client.copied} == {
        "_apps/app/preview/index.tsx",
        "_apps/app/preview/components/Button.tsx",
    }
    assert client.copied[0]["CopySource"]["Bucket"] == "bucket"
    assert client.deleted == [{"Bucket": "bucket", "Key": "_apps/app/preview/old.tsx"}]
    assert invalidated == ["app"]


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


@pytest.mark.asyncio
async def test_sync_preview_compiled_writes_compiled_and_raw_files(monkeypatch) -> None:
    client = _FakeClient(
        list_pages=[
            {
                "Contents": [
                    {"Key": "_repo/apps/demo/index.tsx"},
                    {"Key": "_repo/apps/demo/styles.css"},
                    {"Key": "_repo/apps/demo/broken.ts"},
                ],
                "IsTruncated": False,
            },
            {
                "Contents": [
                    {"Key": "_apps/app/preview/old.tsx"},
                ],
                "IsTruncated": False,
            },
        ],
        objects={
            "_repo/apps/demo/index.tsx": b"export default function App() {}",
            "_repo/apps/demo/styles.css": b".app { color: red; }",
            "_repo/apps/demo/broken.ts": b"const broken =",
        },
    )
    service = _service(client)
    invalidated: list[str] = []

    class FakeCompiler:
        async def compile_batch(self, batch):
            assert batch == [
                {
                    "path": "index.tsx",
                    "source": "export default function App() {}",
                },
                {"path": "broken.ts", "source": "const broken ="},
            ]
            return [
                SimpleNamespace(
                    path="index.tsx",
                    success=True,
                    compiled="export default 1;",
                    error=None,
                ),
                SimpleNamespace(
                    path="broken.ts",
                    success=False,
                    compiled=None,
                    error="Unexpected end of input",
                ),
            ]

    async def invalidate(app_id: str) -> None:
        invalidated.append(app_id)

    monkeypatch.setattr(
        "src.services.app_compiler.AppCompilerService",
        lambda: FakeCompiler(),
    )
    monkeypatch.setattr(service, "invalidate_render_cache", invalidate)

    synced, errors = await service.sync_preview_compiled("app", "apps/demo/")

    assert synced == 3
    assert errors == ["broken.ts: Unexpected end of input"]
    puts = {put["Key"]: put["Body"] for put in client.puts}
    assert puts == {
        "_apps/app/preview/index.tsx": b"export default 1;",
        "_apps/app/preview/styles.css": b".app { color: red; }",
        "_apps/app/preview/broken.ts": b"const broken =",
    }
    assert client.deleted == [{"Bucket": "bucket", "Key": "_apps/app/preview/old.tsx"}]
    assert invalidated == ["app"]


@pytest.mark.asyncio
async def test_render_cache_get_set_and_invalidate_use_expected_redis_keys(monkeypatch):
    redis = _FakeRedis()

    async def get_shared_redis():
        return redis

    monkeypatch.setattr("src.core.cache.get_shared_redis", get_shared_redis)
    service = _service()

    assert await service.get_render_cache("app", "preview") is None
    await service.set_render_cache("app", "preview", {"index.tsx": "code"})
    assert redis.set_calls == [
        ("bifrost:app_render:app:preview", '{"index.tsx": "code"}')
    ]

    assert await service.get_render_cache("app", "preview") == {"index.tsx": "code"}
    await service.invalidate_render_cache("app")
    assert redis.delete_calls == [
        (
            "bifrost:app_render:app:preview",
            "bifrost:app_render:app:live",
        )
    ]


@pytest.mark.asyncio
async def test_render_cache_methods_tolerate_redis_failures(monkeypatch):
    async def get_shared_redis():
        raise RuntimeError("redis offline")

    monkeypatch.setattr("src.core.cache.get_shared_redis", get_shared_redis)
    service = _service()

    assert await service.get_render_cache("app", "live") is None
    await service.set_render_cache("app", "live", {"index.tsx": "code"})
    await service.invalidate_render_cache("app")


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
        read_error: Exception | None = None,
        delete_error: Exception | None = None,
    ) -> None:
        self.list_pages = list(list_pages or [])
        self.objects = objects or {}
        self.read_error = read_error
        self.delete_error = delete_error
        self.list_calls: list[dict] = []
        self.copied: list[dict] = []
        self.deleted: list[dict] = []
        self.puts: list[dict] = []
        self.exceptions = SimpleNamespace(NoSuchKey=KeyError)

    async def list_objects_v2(self, **kwargs):
        self.list_calls.append(kwargs)
        return self.list_pages.pop(0)

    async def get_object(self, *, Bucket: str, Key: str):
        if self.read_error is not None:
            raise self.read_error
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": _Body(self.objects[Key])}

    async def copy_object(self, **kwargs):
        self.copied.append(kwargs)

    async def delete_object(self, **kwargs):
        self.deleted.append(kwargs)
        if self.delete_error is not None:
            raise self.delete_error

    async def put_object(self, **kwargs):
        self.puts.append(kwargs)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str]] = []
        self.delete_calls: list[tuple[str, ...]] = []

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str):
        self.values[key] = value
        self.set_calls.append((key, value))

    async def delete(self, *keys: str):
        self.delete_calls.append(keys)
        for key in keys:
            self.values.pop(key, None)
