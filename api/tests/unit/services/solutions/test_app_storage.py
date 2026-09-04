"""App artifacts use the configured storage for every deployment lifecycle."""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.file_storage.azure_blob_client import AzureBlobStorageClient
from src.services.file_storage.s3_client import S3StorageClient
from src.services.solutions.app_build import SolutionAppBuilder


def _settings(provider: str):
    return cast(
        Any,
        SimpleNamespace(
            object_storage_provider=provider,
            azure_blob_account_url="https://acct.blob.core.windows.net",
            azure_blob_container="azure-container",
            s3_bucket="s3-bucket",
        ),
    )


@pytest.mark.parametrize(
    ("provider", "client_type", "bucket"),
    [
        ("azure_blob", AzureBlobStorageClient, "azure-container"),
        ("s3", S3StorageClient, "s3-bucket"),
    ],
)
def test_app_storage_selects_provider(provider, client_type, bucket):
    builder = SolutionAppBuilder(settings=_settings(provider))
    assert isinstance(builder._storage, client_type)
    assert builder._bucket == bucket


def test_app_storage_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unsupported object_storage_provider"):
        SolutionAppBuilder(settings=_settings("filesystem"))


@pytest.mark.parametrize("provider", ["azure_blob", "s3"])
async def test_app_artifact_lifecycle_uses_selected_provider(monkeypatch, provider):
    if provider == "azure_blob":
        def reject_aws_session():
            raise AssertionError("Azure app artifacts must not create an AWS session")

        monkeypatch.setattr("aiobotocore.session.get_session", reject_aws_session)
        monkeypatch.setattr(
            "src.services.file_storage.s3_client._get_shared_session",
            reject_aws_session,
        )
    builder = SolutionAppBuilder(settings=_settings(provider))
    expected_bucket = builder._bucket
    app_id, other_app, deployment_id = uuid4(), uuid4(), uuid4()
    objects = {f"_apps/{other_app}/dist/index.html": b"other app"}

    class Client:
        async def put_object(self, *, Bucket, Key, Body):
            assert Bucket == expected_bucket
            objects[Key] = Body

        async def get_object(self, *, Bucket, Key):
            assert Bucket == expected_bucket
            return {"Body": SimpleNamespace(read=AsyncMock(return_value=objects[Key]))}

        async def list_objects_v2(self, *, Bucket, Prefix, **kwargs):
            assert Bucket == expected_bucket
            return {"Contents": [{"Key": key} for key in objects if key.startswith(Prefix)]}

        async def delete_object(self, *, Bucket, Key):
            assert Bucket == expected_bucket
            objects.pop(Key, None)

    @asynccontextmanager
    async def client():
        yield Client()

    # Patch the selected adapter, not the builder's client seam: bypassing the
    # adapter (the Azure production regression) must fail this test.
    monkeypatch.setattr(builder._storage, "get_client", client)
    await builder.upload_dist(app_id, {"index.html": b"old", "stale.js": b"stale"})
    await builder.upload_deployment(app_id, deployment_id, {"index.html": b"versioned"})
    await builder.upload_dist(app_id, {"index.html": b"new"})
    assert await builder.list_dist(app_id) == ["index.html"]
    assert await builder.read_dist(app_id, "index.html") == b"new"
    assert await builder.read_dist(app_id, "index.html", deployment_id=deployment_id) == b"versioned"
    await builder.delete_dist(app_id)
    assert await builder.list_dist(app_id) == []
    assert await builder.list_dist(app_id, deployment_id=deployment_id) == ["index.html"]
    await builder.delete_deployment(app_id, deployment_id)
    assert objects == {f"_apps/{other_app}/dist/index.html": b"other app"}
