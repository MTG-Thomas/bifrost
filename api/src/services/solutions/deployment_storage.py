"""Revision-addressed, create-only storage for immutable deployment content."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Callable
from uuid import UUID

from src.config import Settings, get_settings

SOURCE_ARTIFACTS_ROOT = "_solution_artifacts"
SOLUTION_MANIFESTS_ROOT = "_solution_manifests"
SOLUTIONS_ROOT = "_solutions"


class DeploymentArtifactIntegrityError(RuntimeError):
    """A finalized deployment object already exists at the requested key."""


class CreateOnlyArtifactStorage:
    """Shared create-only object writer for immutable platform artifacts."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ):
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

    async def _create(
        self,
        key: str,
        content: bytes,
        content_type: str,
        *,
        idempotent: bool = False,
    ) -> None:
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
                if not self._is_already_exists(exc):
                    raise
                if idempotent:
                    response = await client.get_object(Bucket=self._bucket, Key=key)
                    if await response["Body"].read() == content:
                        return
                raise DeploymentArtifactIntegrityError(
                    f"Immutable artifact object already exists with different bytes: {key}"
                    if idempotent
                    else f"Finalized deployment object already exists: {key}"
                ) from exc

    @staticmethod
    def _is_already_exists(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        status = (
            response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if isinstance(response, dict)
            else None
        )
        code = (
            response.get("Error", {}).get("Code")
            if isinstance(response, dict)
            else None
        )
        return (
            status in {409, 412}
            or code in {"PreconditionFailed", "BlobAlreadyExists"}
            or type(exc).__name__ in {"PreconditionFailed", "ResourceExistsError"}
        )


def deployment_source_artifact_key(
    solution_id: UUID | str, deployment_id: UUID | str
) -> str:
    return f"{SOURCE_ARTIFACTS_ROOT}/{solution_id}/{deployment_id}/source.zip"


def deployment_manifest_key(solution_id: UUID | str, deployment_id: UUID | str) -> str:
    return f"{SOLUTION_MANIFESTS_ROOT}/{solution_id}/{deployment_id}/manifest.json"


def deployment_runtime_prefix(
    solution_id: UUID | str, deployment_id: UUID | str
) -> str:
    return f"{SOLUTIONS_ROOT}/{solution_id}/{deployment_id}/"


class SolutionDeploymentStorage(CreateOnlyArtifactStorage):
    """Writes finalized deployment objects exactly once."""

    def __init__(
        self,
        solution_id: UUID | str,
        deployment_id: UUID | str,
        settings: Settings | None = None,
        client_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ):
        self.solution_id = str(solution_id)
        self.deployment_id = str(deployment_id)
        super().__init__(settings=settings, client_factory=client_factory)

    @property
    def source_artifact_key(self) -> str:
        return deployment_source_artifact_key(self.solution_id, self.deployment_id)

    @property
    def manifest_key(self) -> str:
        return deployment_manifest_key(self.solution_id, self.deployment_id)

    @property
    def runtime_prefix(self) -> str:
        return deployment_runtime_prefix(self.solution_id, self.deployment_id)

    async def write_source_artifact(self, content: bytes) -> str:
        await self._create(self.source_artifact_key, content, "application/zip")
        return self.source_artifact_key

    async def write_compiled_manifest(self, content: bytes) -> str:
        await self._create(self.manifest_key, content, "application/json")
        return self.manifest_key

    async def write_runtime_file(self, path: str, content: bytes) -> str:
        normalized = path.replace("\\", "/").lstrip("/")
        if not normalized or any(
            part in {"", ".", ".."} for part in normalized.split("/")
        ):
            raise ValueError(f"Invalid deployment runtime path: {path!r}")
        key = f"{self.runtime_prefix}{normalized}"
        await self._create(key, content, "application/octet-stream")
        return key
