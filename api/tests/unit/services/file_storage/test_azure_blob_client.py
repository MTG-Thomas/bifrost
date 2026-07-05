from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.file_storage.azure_blob_client import AzureBlobStorageClient


def _settings(**overrides):
    values = {
        "azure_blob_account_url": "https://acct.blob.core.windows.net",
        "azure_blob_container": "files",
        "azure_blob_auth": "account_key",
        "azure_blob_account_key": "key",
        "azure_blob_configured": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_parse_account_name_and_upload_headers() -> None:
    client = AzureBlobStorageClient(_settings())

    assert client._account_name == "acct"
    assert client._parse_account_name("not a url") == ""
    assert client.presigned_upload_headers("text/plain") == {
        "Content-Type": "text/plain",
        "x-ms-blob-type": "BlockBlob",
    }


@pytest.mark.asyncio
async def test_list_objects_v2_shapes_contents_prefixes_and_continuation_token() -> None:
    client = AzureBlobStorageClient(_settings())

    class FakePager:
        continuation_token = "next-token"

        def __init__(self):
            self._sent = False

        def by_page(self, continuation_token=None):
            assert continuation_token == "incoming-token"
            return self

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            return [
                SimpleNamespace(
                    name="forms/a.txt",
                    size=10,
                    etag='"etag-a"',
                    last_modified="today",
                ),
                SimpleNamespace(name="forms/nested/b.txt", size=20, etag='"etag-b"'),
            ]

    class FakeContainer:
        def list_blobs(self, **kwargs):
            assert kwargs == {
                "name_starts_with": "forms/",
                "results_per_page": 2,
            }
            return FakePager()

    client._container_client = FakeContainer()

    result = await client.list_objects_v2(
        Bucket="ignored",
        Prefix="forms/",
        Delimiter="/",
        ContinuationToken="incoming-token",
        MaxKeys=2,
    )

    assert result == {
        "Contents": [
            {
                "Key": "forms/a.txt",
                "Size": 10,
                "ETag": "etag-a",
                "LastModified": "today",
            }
        ],
        "IsTruncated": True,
        "CommonPrefixes": [{"Prefix": "forms/nested/"}],
        "NextContinuationToken": "next-token",
    }


@pytest.mark.asyncio
async def test_ensure_client_rejects_unconfigured_or_unknown_auth_mode() -> None:
    client = AzureBlobStorageClient(_settings(azure_blob_configured=False))

    with pytest.raises(RuntimeError, match="not configured"):
        await client._ensure_client()

    client = AzureBlobStorageClient(_settings(azure_blob_auth="managed_identity"))

    with pytest.raises(RuntimeError, match="Unsupported Azure Blob auth mode"):
        await client._ensure_client()


@pytest.mark.asyncio
async def test_get_and_head_object_translate_resource_not_found(monkeypatch) -> None:
    class ResourceNotFoundError(Exception):
        pass

    monkeypatch.setitem(
        sys.modules,
        "azure.core.exceptions",
        SimpleNamespace(ResourceNotFoundError=ResourceNotFoundError),
    )

    class FakeBlobClient:
        async def get_blob_properties(self):
            raise ResourceNotFoundError("gone")

    class FakeContainer:
        async def download_blob(self, key):
            raise ResourceNotFoundError(key)

        def get_blob_client(self, key):
            return FakeBlobClient()

    client = AzureBlobStorageClient(_settings())
    client._container_client = FakeContainer()

    with pytest.raises(client.exceptions.NoSuchKey, match="missing.txt"):
        await client.get_object(Bucket="ignored", Key="missing.txt")

    with pytest.raises(client.exceptions.NoSuchKey, match="missing.txt"):
        await client.head_object(Bucket="ignored", Key="missing.txt")


@pytest.mark.asyncio
async def test_head_object_returns_s3_shaped_properties(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "azure.core.exceptions",
        SimpleNamespace(ResourceNotFoundError=RuntimeError),
    )

    class FakeBlobClient:
        async def get_blob_properties(self):
            return SimpleNamespace(
                size=42,
                etag='"etag-value"',
                content_settings=SimpleNamespace(content_type="text/plain"),
            )

    class FakeContainer:
        def get_blob_client(self, key):
            assert key == "docs/readme.txt"
            return FakeBlobClient()

    client = AzureBlobStorageClient(_settings())
    client._container_client = FakeContainer()

    result = await client.head_object(Bucket="ignored", Key="docs/readme.txt")

    assert result.content_length == 42
    assert result.content_type == "text/plain"
    assert result.etag == "etag-value"


@pytest.mark.asyncio
async def test_copy_object_polls_until_success(monkeypatch) -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("src.services.file_storage.azure_blob_client.asyncio.sleep", sleep)

    class FakeDestBlob:
        def __init__(self):
            self.polls = [
                SimpleNamespace(copy=SimpleNamespace(status="pending")),
                SimpleNamespace(copy=SimpleNamespace(status="success")),
            ]

        async def start_copy_from_url(self, source_url):
            assert source_url == "https://source-url"
            return {"copy_status": "pending"}

        async def get_blob_properties(self):
            return self.polls.pop(0)

    dest_blob = FakeDestBlob()

    class FakeContainer:
        def get_blob_client(self, key):
            assert key == "dest.txt"
            return dest_blob

    client = AzureBlobStorageClient(_settings())
    client._container_client = FakeContainer()
    client.generate_presigned_download_url = AsyncMock(return_value="https://source-url")

    await client.copy_object(
        Bucket="ignored",
        CopySource={"Bucket": "ignored", "Key": "src.txt"},
        Key="dest.txt",
    )

    client.generate_presigned_download_url.assert_awaited_once_with("src.txt")
    assert sleeps == [0.5]


@pytest.mark.asyncio
async def test_copy_object_raises_on_failed_copy(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.file_storage.azure_blob_client.asyncio.sleep",
        AsyncMock(),
    )

    class FakeDestBlob:
        async def start_copy_from_url(self, source_url):
            return {"copy_status": "pending"}

        async def get_blob_properties(self):
            return SimpleNamespace(copy=SimpleNamespace(status="failed"))

    class FakeContainer:
        def get_blob_client(self, key):
            return FakeDestBlob()

    client = AzureBlobStorageClient(_settings())
    client._container_client = FakeContainer()
    client.generate_presigned_download_url = AsyncMock(return_value="https://source-url")

    with pytest.raises(RuntimeError, match="Azure Blob copy failed"):
        await client.copy_object(
            Bucket="ignored",
            CopySource={"Bucket": "ignored", "Key": "src.txt"},
            Key="dest.txt",
        )


@pytest.mark.asyncio
async def test_generate_blob_sas_uses_account_key_and_content_type(monkeypatch) -> None:
    generated_args: dict = {}

    def generate_blob_sas(**kwargs):
        generated_args.update(kwargs)
        return "sas-token"

    monkeypatch.setitem(
        sys.modules,
        "azure.storage.blob",
        SimpleNamespace(generate_blob_sas=generate_blob_sas),
    )

    client = AzureBlobStorageClient(_settings())

    assert await client._generate_blob_sas(
        "uploads/file.txt",
        permissions="write",
        expires_in=60,
        content_type="text/plain",
    ) == "sas-token"
    assert generated_args["account_name"] == "acct"
    assert generated_args["container_name"] == "files"
    assert generated_args["blob_name"] == "uploads/file.txt"
    assert generated_args["permission"] == "write"
    assert generated_args["account_key"] == "key"
    assert generated_args["content_type"] == "text/plain"


@pytest.mark.asyncio
async def test_read_uploaded_file_maps_missing_blob_to_file_not_found() -> None:
    client = AzureBlobStorageClient(_settings())
    client.get_object = AsyncMock(side_effect=client.exceptions.NoSuchKey("upload.bin"))

    with pytest.raises(FileNotFoundError, match="Uploaded file not found: upload.bin"):
        await client.read_uploaded_file("upload.bin")
