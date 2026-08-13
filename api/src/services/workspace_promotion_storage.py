"""Create-only content-addressed storage for Workspace promotion previews."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Callable
from uuid import UUID

from src.config import Settings
from src.services.solutions.deployment_storage import CreateOnlyArtifactStorage

PROMOTION_ARTIFACTS_ROOT = "_workspace_promotion_artifacts"


def _candidate_digest(candidate_id: str) -> str:
    prefix, separator, digest = candidate_id.partition(":")
    if prefix != "sha256" or not separator or len(digest) != 64:
        raise ValueError("candidate_id must be a sha256 digest")
    if any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("candidate_id must be a lowercase sha256 digest")
    return digest


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
