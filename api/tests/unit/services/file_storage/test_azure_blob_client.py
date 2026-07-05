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
async def test_put_and_delete_object_use_blob_content_settings(monkeypatch) -> None:
    uploaded: dict = {}
    deleted: list[str] = []

    class ContentSettings:
        def __init__(self, content_type):
            self.content_type = content_type

    monkeypatch.setitem(
        sys.modules,
        "azure.storage.blob",
        SimpleNamespace(ContentSettings=ContentSettings),
    )
    monkeypatch.setitem(
        sys.modules,
        "azure.core.exceptions",
        SimpleNamespace(ResourceNotFoundError=RuntimeError),
    )

    class FakeContainer:
        async def upload_blob(self, **kwargs):
            uploaded.update(kwargs)

        async def delete_blob(self, key):
            deleted.append(key)

    client = AzureBlobStorageClient(_settings())
    client._container_client = FakeContainer()

    await client.put_object(
        Bucket="ignored",
        Key="docs/readme.md",
        Body=b"# docs",
        ContentType="text/markdown",
    )
    await client.delete_object(Bucket="ignored", Key="docs/readme.md")

    assert uploaded["name"] == "docs/readme.md"
    assert uploaded["data"] == b"# docs"
    assert uploaded["overwrite"] is True
    assert uploaded["content_settings"].content_type == "text/markdown"
    assert deleted == ["docs/readme.md"]


@pytest.mark.asyncio
async def test_delete_object_ignores_missing_blob(monkeypatch) -> None:
    class ResourceNotFoundError(Exception):
        pass

    monkeypatch.setitem(
        sys.modules,
        "azure.core.exceptions",
        SimpleNamespace(ResourceNotFoundError=ResourceNotFoundError),
    )

    class FakeContainer:
        async def delete_blob(self, key):
            raise ResourceNotFoundError(key)

    client = AzureBlobStorageClient(_settings())
    client._container_client = FakeContainer()

    await client.delete_object(Bucket="ignored", Key="already-gone.txt")


@pytest.mark.asyncio
async def test_copy_object_times_out_when_copy_never_finishes(monkeypatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("src.services.file_storage.azure_blob_client.asyncio.sleep", sleep)

    class FakeDestBlob:
        async def start_copy_from_url(self, source_url):
            return {"copy_status": "pending"}

        async def get_blob_properties(self):
            return SimpleNamespace(copy=SimpleNamespace(status="pending"))

    class FakeContainer:
        def get_blob_client(self, key):
            return FakeDestBlob()

    client = AzureBlobStorageClient(_settings())
    client._container_client = FakeContainer()
    client.generate_presigned_download_url = AsyncMock(return_value="https://source-url")

    with pytest.raises(TimeoutError, match="did not complete"):
        await client.copy_object(
            Bucket="ignored",
            CopySource={"Bucket": "ignored", "Key": "src.txt"},
            Key="dest.txt",
        )

    assert sleep.await_count == 30


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
async def test_generate_blob_sas_uses_user_delegation_key_for_default_credential(
    monkeypatch,
) -> None:
    generated_args: dict = {}

    def generate_blob_sas(**kwargs):
        generated_args.update(kwargs)
        return "delegated-sas"

    monkeypatch.setitem(
        sys.modules,
        "azure.storage.blob",
        SimpleNamespace(generate_blob_sas=generate_blob_sas),
    )

    service_client = SimpleNamespace(
        get_user_delegation_key=AsyncMock(return_value="delegation-key")
    )
    client = AzureBlobStorageClient(_settings(azure_blob_auth="default_credential"))
    client._service_client = service_client

    sas = await client._generate_blob_sas(
        "downloads/file.txt",
        permissions="read",
        expires_in=60,
    )

    assert sas == "delegated-sas"
    assert generated_args["user_delegation_key"] == "delegation-key"
    assert "account_key" not in generated_args
    service_client.get_user_delegation_key.assert_awaited_once()


@pytest.mark.asyncio
async def test_presigned_urls_use_container_blob_urls_and_sas(monkeypatch) -> None:
    class BlobSasPermissions:
        def __init__(self, **flags):
            self.flags = flags

    monkeypatch.setitem(
        sys.modules,
        "azure.storage.blob",
        SimpleNamespace(BlobSasPermissions=BlobSasPermissions),
    )

    class FakeContainer:
        def get_blob_client(self, path):
            return SimpleNamespace(url=f"https://acct.blob.core.windows.net/files/{path}")

    client = AzureBlobStorageClient(_settings())
    client._container_client = FakeContainer()
    client._generate_blob_sas = AsyncMock(side_effect=["upload-sas", "download-sas"])

    upload_url = await client.generate_presigned_upload_url(
        "uploads/a.txt",
        "text/plain",
        expires_in=120,
    )
    download_url = await client.generate_presigned_download_url(
        "uploads/a.txt",
        expires_in=300,
    )

    assert upload_url == "https://acct.blob.core.windows.net/files/uploads/a.txt?upload-sas"
    assert download_url == "https://acct.blob.core.windows.net/files/uploads/a.txt?download-sas"
    upload_call, download_call = client._generate_blob_sas.await_args_list
    assert upload_call.args == ("uploads/a.txt",)
    assert upload_call.kwargs["permissions"].flags == {"write": True, "create": True}
    assert upload_call.kwargs["expires_in"] == 120
    assert upload_call.kwargs["content_type"] == "text/plain"
    assert download_call.args == ("uploads/a.txt",)
    assert download_call.kwargs["permissions"].flags == {"read": True}
    assert download_call.kwargs["expires_in"] == 300


@pytest.mark.asyncio
async def test_read_uploaded_file_maps_missing_blob_to_file_not_found() -> None:
    client = AzureBlobStorageClient(_settings())
    client.get_object = AsyncMock(side_effect=client.exceptions.NoSuchKey("upload.bin"))

    with pytest.raises(FileNotFoundError, match="Uploaded file not found: upload.bin"):
        await client.read_uploaded_file("upload.bin")
