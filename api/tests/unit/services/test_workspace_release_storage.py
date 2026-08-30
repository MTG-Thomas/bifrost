"""Create-only Workspace release tree storage contracts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.services.solutions.deployment_storage import DeploymentArtifactIntegrityError
from src.services.workspace_release_storage import WorkspaceReleaseStorage


class Exists(Exception):
    response = {"ResponseMetadata": {"HTTPStatusCode": 412}}


class Client:
    def __init__(self):
        self.objects = {}

    async def put_object(self, *, Key, Body, **_kwargs):
        if Key in self.objects:
            raise Exists()
        self.objects[Key] = Body

    async def get_object(self, *, Key, **_kwargs):
        content = self.objects[Key]

        class Body:
            async def read(self):
                return content

        return {"Body": Body()}

    async def list_objects_v2(self, *, Prefix, **_kwargs):
        return {
            "Contents": [
                {"Key": key} for key in sorted(self.objects) if key.startswith(Prefix)
            ],
            "IsTruncated": False,
        }


@asynccontextmanager
async def factory(client):
    yield client


@pytest.mark.asyncio
async def test_release_files_are_create_only_and_idempotent() -> None:
    client = Client()
    storage = WorkspaceReleaseStorage(
        "_workspace_releases/org/" + "a" * 64 + "/files/",
        settings=SimpleNamespace(object_storage_provider="s3", s3_bucket="test"),
        client_factory=lambda: factory(client),
    )

    await storage.write("workflows/demo.py", b"same")
    await storage.write("workflows/demo.py", b"same")

    assert await storage.read("workflows/demo.py") == b"same"
    with pytest.raises(DeploymentArtifactIntegrityError, match="different bytes"):
        await storage.write("workflows/demo.py", b"different")


@pytest.mark.asyncio
async def test_release_list_uses_injected_storage_client() -> None:
    client = Client()
    storage = WorkspaceReleaseStorage(
        "_workspace_releases/org/" + "a" * 64 + "/files/",
        settings=SimpleNamespace(object_storage_provider="s3", s3_bucket="test"),
        client_factory=lambda: factory(client),
    )
    await storage.write("modules/shared.py", b"shared")
    await storage.write("workflows/demo.py", b"demo")

    assert await storage.list() == ["modules/shared.py", "workflows/demo.py"]
