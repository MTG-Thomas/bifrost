"""Create-only storage guarantees for promotion artifacts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.solutions.deployment_storage import DeploymentArtifactIntegrityError
from src.services.workspace_promotion_storage import WorkspacePromotionArtifactStorage


class _Exists(Exception):
    response = {"ResponseMetadata": {"HTTPStatusCode": 412}}


class _Client:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def put_object(self, *, Key: str, Body: bytes, **_kwargs) -> None:
        if Key in self.objects:
            raise _Exists()
        self.objects[Key] = Body

    async def get_object(self, *, Key: str, **_kwargs):
        content = self.objects[Key]

        class _Body:
            async def read(self) -> bytes:
                return content

        return {"Body": _Body()}

    async def delete_object(self, *, Key: str, **_kwargs) -> None:
        self.objects.pop(Key, None)


@asynccontextmanager
async def _factory(client: _Client):
    yield client


async def test_duplicate_exact_artifact_is_idempotent() -> None:
    client = _Client()
    storage = WorkspacePromotionArtifactStorage(
        uuid4(),
        "sha256:" + "a" * 64,
        settings=SimpleNamespace(object_storage_provider="s3", s3_bucket="test"),
        client_factory=lambda: _factory(client),
    )

    await storage.write_source(b"same")
    await storage.write_source(b"same")

    assert client.objects[storage.source_artifact_key] == b"same"


async def test_candidate_key_collision_with_different_bytes_fails_closed() -> None:
    client = _Client()
    storage = WorkspacePromotionArtifactStorage(
        uuid4(),
        "sha256:" + "b" * 64,
        settings=SimpleNamespace(object_storage_provider="s3", s3_bucket="test"),
        client_factory=lambda: _factory(client),
    )
    await storage.write_source(b"first")

    with pytest.raises(DeploymentArtifactIntegrityError, match="different bytes"):
        await storage.write_source(b"second")


async def test_expired_draft_cleanup_deletes_only_exact_artifact_objects() -> None:
    client = _Client()
    storage = WorkspacePromotionArtifactStorage(
        uuid4(),
        "sha256:" + "c" * 64,
        settings=SimpleNamespace(object_storage_provider="s3", s3_bucket="test"),
        client_factory=lambda: _factory(client),
    )
    await storage.write_source(b"source")
    await storage.write_manifest(b"manifest")
    client.objects["unrelated"] = b"preserve"

    await storage.delete_expired_draft()

    assert client.objects == {"unrelated": b"preserve"}
