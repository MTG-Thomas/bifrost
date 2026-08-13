"""Create-only content-addressed storage for Workspace promotion previews."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Callable
from uuid import UUID

from src.config import Settings, get_settings
from src.services.solutions.deployment_storage import DeploymentArtifactIntegrityError

PROMOTION_ARTIFACTS_ROOT = "_workspace_promotion_artifacts"


def _candidate_digest(candidate_id: str) -> str:
    prefix, separator, digest = candidate_id.partition(":")
    if prefix != "sha256" or not separator or len(digest) != 64:
        raise ValueError("candidate_id must be a sha256 digest")
    if any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("candidate_id must be a lowercase sha256 digest")
    return digest


class WorkspacePromotionArtifactStorage:
    """Write an immutable source archive and manifest exactly once."""

    def __init__(
        self,
        organization_id: UUID | str,
        candidate_id: str,
        settings: Settings | None = None,
        client_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ):
        digest = _candidate_digest(candidate_id)
        self.prefix = f"{PROMOTION_ARTIFACTS_ROOT}/{organization_id}/{digest}"
        self.settings = settings or get_settings()
        if client_factory is None:
            if self.settings.object_storage_provider == "azure_blob":
                from src.services.file_storage.azure_blob_client import (
                    AzureBlobStorageClient,
                )

                storage = AzureBlobStorageClient(self.settings)
            else:
                from src.services.file_storage.s3_client import S3StorageClient

                storage = S3StorageClient(self.settings)
            client_factory = storage.get_client
        self._client_factory = client_factory
        self._bucket = (
            self.settings.azure_blob_container
            if self.settings.object_storage_provider == "azure_blob"
            else self.settings.s3_bucket
        ) or ""

    @property
    def source_artifact_key(self) -> str:
        return f"{self.prefix}/source.zip"

    @property
    def manifest_key(self) -> str:
        return f"{self.prefix}/manifest.json"

    async def write_source(self, content: bytes) -> str:
        await self._create(self.source_artifact_key, content, "application/zip")
        return self.source_artifact_key

    async def write_manifest(self, content: bytes) -> str:
        await self._create(self.manifest_key, content, "application/json")
        return self.manifest_key

    async def _create(self, key: str, content: bytes, content_type: str) -> None:
        async with self._client_factory() as client:
            try:
                await client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=content,
                    ContentType=content_type,
                    IfNoneMatch="*",
                )
            except Exception as exc:  # noqa: BLE001 - storage backends differ
                if self._is_already_exists(exc):
                    response = await client.get_object(Bucket=self._bucket, Key=key)
                    existing = await response["Body"].read()
                    if existing == content:
                        return
                    raise DeploymentArtifactIntegrityError(
                        f"Workspace promotion object has different bytes: {key}"
                    ) from exc
                raise

    @staticmethod
    def _is_already_exists(exc: Exception) -> bool:
        return type(exc).__name__ in {
            "PreconditionFailed",
            "ResourceExistsError",
        } or (
            isinstance(getattr(exc, "response", None), dict)
            and (
                exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                in {409, 412}
                or exc.response.get("Error", {}).get("Code")
                in {"PreconditionFailed", "BlobAlreadyExists"}
            )
        )
