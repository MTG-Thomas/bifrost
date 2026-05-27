from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.config import Settings
from src.services.file_storage.azure_blob_client import AzureBlobStorageClient
from src.services.file_storage.s3_client import S3StorageClient
from src.services.file_storage.service import FileStorageService


def _settings(**overrides):
    values = {
        "secret_key": "x" * 32,
    }
    values.update(overrides)
    return Settings(**values)


def test_file_storage_uses_s3_backend_by_default():
    service = FileStorageService(db=MagicMock(), settings=_settings())

    assert isinstance(service._s3_storage, S3StorageClient)


def test_file_storage_uses_azure_blob_backend_when_configured():
    service = FileStorageService(
        db=MagicMock(),
        settings=_settings(
            object_storage_provider="azure_blob",
            azure_blob_account_url="https://example.blob.core.windows.net",
            azure_blob_container="bifrost-objects",
            azure_blob_auth="default_credential",
        ),
    )

    assert isinstance(service._s3_storage, AzureBlobStorageClient)
    assert service.presigned_upload_headers("text/plain") == {
        "Content-Type": "text/plain",
        "x-ms-blob-type": "BlockBlob",
    }


@pytest.mark.asyncio
async def test_azure_blob_client_exposes_s3_shaped_list_objects_v2():
    client = AzureBlobStorageClient(
        _settings(
            object_storage_provider="azure_blob",
            azure_blob_account_url="https://example.blob.core.windows.net",
            azure_blob_container="bifrost-objects",
            azure_blob_auth="default_credential",
        )
    )

    class FakeContainer:
        async def list_blobs(self, name_starts_with=""):
            for name in [
                "_repo/workflows/a.py",
                "_repo/apps/app.tsx",
                "_repo/apps/components/button.tsx",
            ]:
                if name.startswith(name_starts_with):
                    yield SimpleNamespace(
                        name=name,
                        size=10,
                        etag='"etag"',
                        last_modified=None,
                    )

    client._container_client = FakeContainer()

    response = await client.list_objects_v2(
        Bucket="ignored",
        Prefix="_repo/",
        Delimiter="/",
    )

    assert response["IsTruncated"] is False
    assert response["Contents"] == []
    assert response["CommonPrefixes"] == [
        {"Prefix": "_repo/apps/"},
        {"Prefix": "_repo/workflows/"},
    ]

    response = await client.list_objects_v2(
        Bucket="ignored",
        Prefix="_repo/workflows/",
    )

    assert response["Contents"] == [
        {
            "Key": "_repo/workflows/a.py",
            "Size": 10,
            "ETag": "etag",
            "LastModified": None,
        }
    ]
