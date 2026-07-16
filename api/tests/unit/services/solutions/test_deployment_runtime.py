from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest,
    DeploymentGitProvenance,
    DeploymentResolutionMap,
    DeploymentSource,
    RuntimeEntityDefinition,
    RuntimeSourceResolution,
    canonical_json,
    sha256_digest,
)
from src.services.solutions.deployment_runtime import (
    DeploymentRuntimeError,
    pin_workflow_runtime,
    verify_runtime_evidence,
)


class _Result:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row


def _closure(*, deployment_id, solution_id, workflow_id, source_text):
    source_hash = sha256_digest(source_text.encode())
    entity = RuntimeEntityDefinition(
        portable_ref="workflows/demo.py::demo",
        resolved_id=workflow_id,
        source_ref="workflows/demo.py",
        source_hash=source_hash,
        definition={
            "name": f"demo-{source_text}",
            "function_name": "demo",
            "path": "workflows/demo.py",
            "timeout_seconds": 30,
            "type": "workflow",
        },
    )
    resolution = DeploymentResolutionMap(
        workflows={entity.portable_ref: entity},
        sources={
            "workflows/demo.py": RuntimeSourceResolution(
                object_key=f"_solutions/{solution_id}/{deployment_id}/workflows/demo.py",
                content_hash=source_hash,
            )
        },
    )
    resolution_hash = sha256_digest(canonical_json(resolution))
    manifest = CompiledDeploymentManifest(
        solution_id=solution_id,
        deployment_id=deployment_id,
        bundle_hash=sha256_digest(source_text.encode()),
        resolution_map_hash=resolution_hash,
        source=DeploymentSource(
            artifact_key=f"_solution_artifacts/{solution_id}/{deployment_id}/source.zip",
            runtime_prefix=f"_solutions/{solution_id}/{deployment_id}/",
        ),
        workflows={entity.portable_ref: entity},
        git=DeploymentGitProvenance(),
    )
    return SimpleNamespace(
        id=deployment_id,
        solution_id=solution_id,
        state="active",
        bundle_hash=manifest.bundle_hash,
        compiled_manifest=manifest.model_dump(mode="json"),
        compiled_manifest_hash=manifest.content_hash(),
        resolution_map=resolution.model_dump(mode="json"),
        resolution_map_hash=resolution_hash,
        runtime_storage_prefix=manifest.source.runtime_prefix,
        git_commit_sha=None,
        dependencies=[],
    )


@pytest.mark.asyncio
async def test_active_pointer_selects_runtime_without_reading_mutable_definition(
    monkeypatch,
):
    solution_id, workflow_id = uuid4(), uuid4()
    old_id, new_id = uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=solution_id)
    solution = SimpleNamespace(
        id=solution_id,
        status="active",
        organization_id=None,
        global_repo_access=False,
        active_deployment_id=old_id,
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=_Result((workflow, solution)))
    )
    deployments = {
        old_id: _closure(
            deployment_id=old_id,
            solution_id=solution_id,
            workflow_id=workflow_id,
            source_text="old",
        ),
        new_id: _closure(
            deployment_id=new_id,
            solution_id=solution_id,
            workflow_id=workflow_id,
            source_text="new",
        ),
    }

    async def get_closure(_repo, deployment_id, _org_id):
        return deployments[deployment_id]

    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.SolutionDeploymentRepository.get_runtime_closure",
        get_closure,
    )

    old = await pin_workflow_runtime(session, workflow_id)
    solution.active_deployment_id = new_id
    new = await pin_workflow_runtime(session, workflow_id)

    assert old is not None and new is not None
    assert old.deployment_id == old_id
    assert old.name == "demo-old"
    assert new.deployment_id == new_id
    assert new.name == "demo-new"
    # The already materialized queue evidence remains pinned after promotion.
    assert old.queue_evidence()["solution_deployment_id"] == str(old_id)


@pytest.mark.asyncio
async def test_solution_without_active_deployment_never_falls_back_to_mutable_row():
    solution_id, workflow_id = uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=solution_id)
    solution = SimpleNamespace(
        id=solution_id,
        status="active",
        organization_id=None,
        global_repo_access=False,
        active_deployment_id=None,
        execution_runtime_mode="deployment-v1",
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=_Result((workflow, solution)))
    )

    with pytest.raises(DeploymentRuntimeError, match="no active deployment"):
        await pin_workflow_runtime(session, workflow_id)


@pytest.mark.asyncio
async def test_explicit_legacy_solution_without_deployment_uses_repo_compatibility():
    solution_id, workflow_id = uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=solution_id)
    solution = SimpleNamespace(
        id=solution_id,
        status="active",
        organization_id=None,
        global_repo_access=False,
        active_deployment_id=None,
        execution_runtime_mode="repo-v1",
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=_Result((workflow, solution))))

    assert await pin_workflow_runtime(session, workflow_id) is None


def test_tampered_queue_evidence_is_rejected_against_durable_manifest_evidence():
    deployment_id = str(uuid4())
    authoritative = {
        "solution_deployment_id": deployment_id,
        "workflow_path": "workflows/run.py",
    }
    evidence_hash = sha256_digest(canonical_json(authoritative))
    tampered = {**authoritative, "workflow_path": "workflows/other.py"}

    with pytest.raises(DeploymentRuntimeError, match="queue evidence"):
        verify_runtime_evidence(
            deployment_id, tampered, authoritative, evidence_hash, authoritative
        )


@pytest.mark.asyncio
async def test_child_call_inherits_superseded_parent_deployment(monkeypatch):
    solution_id, workflow_id = uuid4(), uuid4()
    old_id, active_id = uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=solution_id)
    solution = SimpleNamespace(
        id=solution_id,
        status="active",
        organization_id=None,
        global_repo_access=False,
        active_deployment_id=active_id,
    )
    old = _closure(
        deployment_id=old_id,
        solution_id=solution_id,
        workflow_id=workflow_id,
        source_text="old",
    )
    old.state = "superseded"
    session = SimpleNamespace(execute=AsyncMock(return_value=_Result((workflow, solution))))

    async def get_by_id(_repo, deployment_id):
        return old if deployment_id == old_id else None

    async def get_closure(_repo, deployment_id, _org):
        return old if deployment_id == old_id else None

    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.SolutionDeploymentRepository.get_by_id_for_runtime",
        get_by_id,
    )
    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.SolutionDeploymentRepository.get_runtime_closure",
        get_closure,
    )
    pinned = await pin_workflow_runtime(
        session, workflow_id, caller_deployment_id=old_id
    )
    assert pinned is not None and pinned.deployment_id == old_id


@pytest.mark.asyncio
async def test_cross_solution_child_uses_exact_dependency_deployment(monkeypatch):
    owner_solution_id, target_solution_id, workflow_id = uuid4(), uuid4(), uuid4()
    caller_id, dependency_id, current_target_id = uuid4(), uuid4(), uuid4()
    workflow = SimpleNamespace(id=workflow_id, solution_id=target_solution_id)
    solution = SimpleNamespace(
        id=target_solution_id,
        status="active",
        organization_id=None,
        global_repo_access=False,
        active_deployment_id=current_target_id,
    )
    caller = SimpleNamespace(
        id=caller_id,
        solution_id=owner_solution_id,
        dependencies=[
            SimpleNamespace(
                dependency_solution_id=target_solution_id,
                dependency_deployment_id=dependency_id,
            )
        ],
    )
    dependency = _closure(
        deployment_id=dependency_id,
        solution_id=target_solution_id,
        workflow_id=workflow_id,
        source_text="dependency",
    )
    dependency.state = "superseded"
    session = SimpleNamespace(execute=AsyncMock(return_value=_Result((workflow, solution))))

    async def get_by_id(_repo, deployment_id):
        return caller if deployment_id == caller_id else None

    async def get_closure(_repo, deployment_id, _org):
        return dependency if deployment_id == dependency_id else None

    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.SolutionDeploymentRepository.get_by_id_for_runtime",
        get_by_id,
    )
    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.SolutionDeploymentRepository.get_runtime_closure",
        get_closure,
    )
    pinned = await pin_workflow_runtime(
        session, workflow_id, caller_deployment_id=caller_id
    )
    assert pinned is not None and pinned.deployment_id == dependency_id
