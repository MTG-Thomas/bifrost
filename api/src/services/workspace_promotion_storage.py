"""Create-only content-addressed storage for Workspace promotion previews."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Callable
from uuid import UUID

from src.config import Settings
from src.services.solutions.deployment_storage import CreateOnlyArtifactStorage

PROMOTION_ARTIFACTS_ROOT = "_workspace_promotion_artifacts"
DRAFT_RUNTIME_ROOT = "_workspace_releases"


def _candidate_digest(candidate_id: str) -> str:
    prefix, separator, digest = candidate_id.partition(":")
    if prefix != "sha256" or not separator or len(digest) != 64:
        raise ValueError("candidate_id must be a sha256 digest")
    if any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("candidate_id must be a lowercase sha256 digest")
    return digest


def workspace_draft_runtime_prefix(
    organization_id: UUID | str, content_id: str
) -> str:
    digest = _candidate_digest(content_id)
    return f"{DRAFT_RUNTIME_ROOT}/{organization_id}/drafts/{digest}/files/"


class WorkspacePromotionArtifactStorage(CreateOnlyArtifactStorage):
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
        super().__init__(settings=settings, client_factory=client_factory)

    @property
    def source_artifact_key(self) -> str:
        return f"{self.prefix}/source.zip"

    @property
    def manifest_key(self) -> str:
        return f"{self.prefix}/manifest.json"

    async def write_source(self, content: bytes) -> str:
        await self._create(
            self.source_artifact_key, content, "application/zip", idempotent=True
        )
        return self.source_artifact_key

    async def write_manifest(self, content: bytes) -> str:
        await self._create(
            self.manifest_key, content, "application/json", idempotent=True
        )
        return self.manifest_key

    async def read_source(self) -> bytes:
        async with self._client_factory() as client:
            response = await client.get_object(
                Bucket=self._bucket, Key=self.source_artifact_key
            )
            return await response["Body"].read()


class WorkspaceDraftRuntimeStorage(CreateOnlyArtifactStorage):
    """Create-only staging for one immutable draft execution closure."""

    def __init__(
        self,
        organization_id: UUID | str,
        content_id: str,
        settings: Settings | None = None,
        client_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ):
        self.runtime_prefix = workspace_draft_runtime_prefix(
            organization_id, content_id
        )
        super().__init__(settings=settings, client_factory=client_factory)

    async def write_file(self, path: str, content: bytes) -> str:
        normalized = path.replace("\\", "/").lstrip("/")
        if not normalized or any(
            part in {"", ".", ".."} for part in normalized.split("/")
        ):
            raise ValueError(f"Invalid draft runtime path: {path!r}")
        key = f"{self.runtime_prefix}{normalized}"
        await self._create(
            key, content, "application/octet-stream", idempotent=True
        )
        return key
