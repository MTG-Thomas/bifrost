"""Stored source artifact for a deployed Solution install.

The artifact is the portable workspace zip that produced the install. Runtime
outputs still live elsewhere: Python execution files under ``_solutions/`` and
app dist under ``_apps/``. Keeping the zip in a separate prefix prevents the
Python full-replace writer from deleting it.
"""

from __future__ import annotations

from uuid import UUID

from src.config import Settings, get_settings

SOURCE_ARTIFACTS_ROOT = "_solution_artifacts"
SOURCE_ARTIFACT_NAME = "source.zip"


class SolutionSourceArtifactStorage:
    """S3 storage for one install's source workspace zip."""

    def __init__(self, solution_id: UUID | str, settings: Settings | None = None):
        self.solution_id = str(solution_id)
        self.prefix = f"{SOURCE_ARTIFACTS_ROOT}/{self.solution_id}/"
        self._settings = settings or get_settings()
        if self._settings.object_storage_provider == "azure_blob":
            from src.services.file_storage.azure_blob_client import (
                AzureBlobStorageClient,
            )

            self._storage = AzureBlobStorageClient(self._settings)
            self._bucket = self._settings.azure_blob_container or ""
        else:
            from src.services.file_storage.s3_client import S3StorageClient

            self._storage = S3StorageClient(self._settings)
            self._bucket = self._settings.s3_bucket or ""

    def _get_client(self):
        return self._storage.get_client()

    def _key(self) -> str:
        return f"{self.prefix}{SOURCE_ARTIFACT_NAME}"

    async def write(self, data: bytes) -> None:
        async with self._get_client() as client:
            await client.put_object(Bucket=self._bucket, Key=self._key(), Body=data)

    async def read(self) -> bytes | None:
        async with self._get_client() as client:
            try:
                response = await client.get_object(Bucket=self._bucket, Key=self._key())
                return await response["Body"].read()
            except client.exceptions.NoSuchKey:
                return None
            except Exception as exc:  # noqa: BLE001 - aiobotocore backends vary
                if "NoSuchKey" in str(type(exc).__name__) or "404" in str(exc):
                    return None
                raise

    async def delete(self) -> None:
        async with self._get_client() as client:
            await client.delete_object(Bucket=self._bucket, Key=self._key())
