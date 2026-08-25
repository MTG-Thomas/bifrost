from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from src.services.file_storage.azure_blob_client import AzureBlobStorageClient
from src.services.file_storage.s3_client import S3StorageClient
from src.services.solutions.deploy_job_storage import SolutionDeployJobStorage


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


def test_deploy_job_storage_uses_configured_azure_blob_provider() -> None:
    storage = SolutionDeployJobStorage(uuid4(), settings=_settings("azure_blob"))

    assert isinstance(storage._storage, AzureBlobStorageClient)
    assert storage._bucket == "azure-container"


def test_deploy_job_storage_uses_configured_s3_provider() -> None:
    storage = SolutionDeployJobStorage(uuid4(), settings=_settings("s3"))

    assert isinstance(storage._storage, S3StorageClient)
    assert storage._bucket == "s3-bucket"


def test_deploy_job_storage_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported object_storage_provider"):
        SolutionDeployJobStorage(uuid4(), settings=_settings("filesystem"))
