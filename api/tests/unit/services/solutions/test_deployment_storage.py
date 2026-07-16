from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from src.services.solutions.deployment_storage import (
    DeploymentArtifactIntegrityError,
    SolutionDeploymentStorage,
)


class ResourceExistsError(Exception):
    pass


class S3PreconditionFailed(Exception):
    def __init__(self):
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": 412},
            "Error": {"Code": "PreconditionFailed"},
        }


class FakeClient:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def put_object(self, *, Key, Body, IfNoneMatch, **kwargs):
        assert IfNoneMatch == "*"
        if Key in self.objects:
            raise ResourceExistsError(Key)
        self.objects[Key] = Body


def make_storage(client: FakeClient):
    @asynccontextmanager
    async def client_factory():
        yield client

    settings = SimpleNamespace(
        object_storage_provider="s3",
        s3_bucket="test",
        azure_blob_container=None,
    )
    return SolutionDeploymentStorage(
        uuid4(), uuid4(), settings=cast(Any, settings), client_factory=client_factory
    )


@pytest.mark.asyncio
async def test_finalized_source_manifest_and_runtime_keys_are_revision_addressed():
    client = FakeClient()
    storage = make_storage(client)

    source_key = await storage.write_source_artifact(b"zip")
    manifest_key = await storage.write_compiled_manifest(b"{}")
    runtime_key = await storage.write_runtime_file("workflows/run.py", b"code")

    assert f"/{storage.deployment_id}/" in source_key
    assert f"/{storage.deployment_id}/" in manifest_key
    assert runtime_key == f"{storage.runtime_prefix}workflows/run.py"
    assert client.objects[runtime_key] == b"code"


@pytest.mark.asyncio
async def test_finalized_objects_are_create_only():
    client = FakeClient()
    storage = make_storage(client)
    await storage.write_compiled_manifest(b"first")

    with pytest.raises(DeploymentArtifactIntegrityError):
        await storage.write_compiled_manifest(b"replacement")

    assert client.objects[storage.manifest_key] == b"first"


@pytest.mark.asyncio
async def test_runtime_path_rejects_traversal():
    storage = make_storage(FakeClient())
    with pytest.raises(ValueError):
        await storage.write_runtime_file("../mutable.py", b"code")


@pytest.mark.parametrize(
    "error",
    [S3PreconditionFailed(), ResourceExistsError("azure duplicate")],
)
def test_provider_duplicate_write_exceptions_are_classified(error):
    assert SolutionDeploymentStorage._is_already_exists(error)
