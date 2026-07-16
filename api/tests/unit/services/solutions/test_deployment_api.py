from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.contracts.solution_deployments import SolutionDeploymentCreate
from src.services.solutions.deployment_api import SolutionDeploymentAPIService
from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest,
    DeploymentResolutionMap,
    DeploymentSource,
    canonical_json,
    sha256_digest,
)
from src.services.solutions.deployment_storage import (
    deployment_source_artifact_key,
    deployment_runtime_prefix,
)


@pytest.mark.asyncio
async def test_create_registers_complete_reference_only_ready_draft(monkeypatch):
    solution_id = uuid4()
    deployment_id = uuid4()
    resolution = DeploymentResolutionMap()
    resolution_hash = sha256_digest(canonical_json(resolution))
    manifest = CompiledDeploymentManifest(
        solution_id=solution_id,
        deployment_id=deployment_id,
        bundle_hash="sha256:bundle",
        resolution_map_hash=resolution_hash,
        source=DeploymentSource(
            artifact_key=deployment_source_artifact_key(solution_id, deployment_id),
            runtime_prefix=deployment_runtime_prefix(solution_id, deployment_id),
        ),
    )
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=SimpleNamespace(organization_id=None))
    )
    created = []
    written = []

    class Repository:
        def __init__(self, _session):
            pass

        async def create(self, deployment):
            created.append(deployment)

    class Storage:
        def __init__(self, *_args):
            pass

        async def write_compiled_manifest(self, content):
            written.append(content)

    monkeypatch.setattr("src.services.solutions.deployment_api.SolutionDeploymentRepository", Repository)
    monkeypatch.setattr("src.services.solutions.deployment_api.SolutionDeploymentStorage", Storage)
    service = SolutionDeploymentAPIService(session)
    row = await service.create_ready_draft(
        solution_id,
        uuid4(),
        SolutionDeploymentCreate(compiled_manifest=manifest, resolution_map=resolution),
    )

    assert row.state == "ready"
    assert row.source_artifact_key == manifest.source.artifact_key
    assert created == [row]
    assert written == [manifest.canonical_bytes()]


@pytest.mark.asyncio
async def test_create_rejects_noncanonical_external_source_reference():
    solution_id = uuid4()
    deployment_id = uuid4()
    resolution = DeploymentResolutionMap()
    manifest = CompiledDeploymentManifest(
        solution_id=solution_id,
        deployment_id=deployment_id,
        bundle_hash="sha256:bundle",
        resolution_map_hash=sha256_digest(canonical_json(resolution)),
        source=DeploymentSource(artifact_key="mutable/source.zip", runtime_prefix="runtime/"),
    )
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=SimpleNamespace(organization_id=None))
    )
    with pytest.raises(ValueError, match="canonical deployment key"):
        await SolutionDeploymentAPIService(session).create_ready_draft(
            solution_id,
            uuid4(),
            SolutionDeploymentCreate(compiled_manifest=manifest, resolution_map=resolution),
        )
