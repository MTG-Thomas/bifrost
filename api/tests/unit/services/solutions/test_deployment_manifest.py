from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.services.solutions.deployment_manifest import (
    CompiledDeploymentManifest,
    DependencyResolution,
    DeploymentResolutionMap,
    DeploymentSource,
    RuntimeEntityDefinition,
    RuntimeSourceResolution,
)


def test_manifest_hash_is_canonical_and_contract_is_frozen():
    solution_id = uuid4()
    deployment_id = uuid4()
    workflow_id = uuid4()
    source = DeploymentSource(
        artifact_key=f"_solution_artifacts/{solution_id}/{deployment_id}/source.zip",
        runtime_prefix=f"_solutions/{solution_id}/{deployment_id}/",
    )
    workflow = RuntimeEntityDefinition(
        portable_ref="workflows/triage.py::run",
        resolved_id=workflow_id,
        definition={"z": 2, "a": 1},
        source_ref="workflows/triage.py",
        source_hash="sha256:abc",
    )
    first = CompiledDeploymentManifest(
        solution_id=solution_id,
        deployment_id=deployment_id,
        bundle_hash="sha256:bundle",
        source=source,
        workflows={workflow.portable_ref: workflow},
    )
    second = CompiledDeploymentManifest.model_validate(first.model_dump(mode="json"))

    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.content_hash() == second.content_hash()
    with pytest.raises(ValidationError):
        first.bundle_hash = "sha256:changed"


def test_resolution_map_uses_only_deployment_local_immutable_evidence():
    dependency_deployment_id = uuid4()
    workflow = RuntimeEntityDefinition(
        portable_ref="workflows/triage.py::run",
        resolved_id=uuid4(),
        definition={"function_name": "run"},
        source_ref="workflows/triage.py",
        source_hash="sha256:source",
    )
    dependency = DependencyResolution(
        solution_id=uuid4(),
        deployment_id=dependency_deployment_id,
        declared_constraint=">=2",
        bundle_hash="sha256:dependency",
    )
    resolution_map = DeploymentResolutionMap(
        workflows={workflow.portable_ref: workflow},
        dependencies={"shared": dependency},
        sources={
            "workflows/triage.py": RuntimeSourceResolution(
                object_key="_solutions/solution/deployment/workflows/triage.py",
                content_hash="sha256:source",
            )
        },
    )

    assert resolution_map.resolve_workflow(workflow.portable_ref) == workflow
    assert resolution_map.resolve_dependency("shared") == dependency_deployment_id
    assert (
        resolution_map.resolve_source("workflows/triage.py").content_hash
        == "sha256:source"
    )


def test_contract_rejects_unknown_mutable_projection_fields():
    with pytest.raises(ValidationError):
        RuntimeEntityDefinition.model_validate(
            {
                "portable_ref": "workflow",
                "resolved_id": uuid4(),
                "definition": {},
                "active_orm_row": {"name": "mutable"},
            }
        )
