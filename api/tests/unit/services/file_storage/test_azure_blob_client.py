from __future__ import annotations

from types import SimpleNamespace

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
