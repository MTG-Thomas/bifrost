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
    canonical_json,
    sha256_digest,
    validate_runtime_closure,
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
        definition={"z": 2, "a": {"values": [1, 2]}},
        source_ref="workflows/triage.py",
        source_hash="sha256:abc",
    )
    resolution = DeploymentResolutionMap(
        workflows={workflow.portable_ref: workflow},
        sources={
            "workflows/triage.py": RuntimeSourceResolution(
                object_key="runtime/workflows/triage.py", content_hash="sha256:abc"
            )
        },
    )
    resolution_hash = sha256_digest(canonical_json(resolution))
    first = CompiledDeploymentManifest(
        solution_id=solution_id,
        deployment_id=deployment_id,
        bundle_hash="sha256:bundle",
        resolution_map_hash=resolution_hash,
        source=source,
        workflows={workflow.portable_ref: workflow},
    )
    second = CompiledDeploymentManifest.model_validate(first.model_dump(mode="json"))

    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.content_hash() == second.content_hash()
    with pytest.raises(ValidationError):
        first.bundle_hash = "sha256:changed"
    original_hash = first.content_hash()
    with pytest.raises(TypeError, match="immutable"):
        workflow.definition["new"] = "value"
    nested = workflow.definition["a"]
    assert isinstance(nested, dict)
    with pytest.raises(TypeError, match="immutable"):
        nested["new"] = "value"
    values = nested["values"]
    assert isinstance(values, tuple)
    with pytest.raises(AttributeError):
        values.append(3)
    with pytest.raises(TypeError, match="immutable"):
        first.workflows["replacement"] = workflow
    assert first.content_hash() == original_hash


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


def test_canonical_json_rejects_non_json_numbers():
    with pytest.raises(ValueError):
        canonical_json({"bad": float("nan")})


def test_runtime_closure_anchors_resolution_and_supports_id_lookup():
    solution_id = uuid4()
    deployment_id = uuid4()
    workflow = RuntimeEntityDefinition(
        portable_ref="workflows/run.py::run",
        resolved_id=uuid4(),
        definition={"function_name": "run"},
        source_ref="workflows/run.py",
        source_hash="sha256:source",
    )
    resolution = DeploymentResolutionMap(
        workflows={workflow.portable_ref: workflow},
        sources={
            "workflows/run.py": RuntimeSourceResolution(
                object_key="runtime/workflows/run.py", content_hash="sha256:source"
            )
        },
    )
    resolution_hash = sha256_digest(canonical_json(resolution))
    manifest = CompiledDeploymentManifest(
        solution_id=solution_id,
        deployment_id=deployment_id,
        bundle_hash="sha256:bundle",
        resolution_map_hash=resolution_hash,
        source=DeploymentSource(artifact_key="source.zip", runtime_prefix="runtime/"),
        workflows={workflow.portable_ref: workflow},
    )

    validate_runtime_closure(
        manifest,
        resolution,
        [],
        expected_manifest_hash=manifest.content_hash(),
        expected_resolution_hash=resolution_hash,
    )
    assert resolution.resolve_workflow_id(workflow.resolved_id) == workflow

    changed = resolution.model_copy(update={"workflows": {}})
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_runtime_closure(
            manifest,
            changed,
            [],
            expected_manifest_hash=manifest.content_hash(),
            expected_resolution_hash=resolution_hash,
        )


def test_runtime_closure_rejects_undeclared_dependency_owner():
    dependency_solution_id = uuid4()
    workflow = RuntimeEntityDefinition(
        portable_ref="workflows/run.py::run",
        resolved_id=uuid4(),
        definition={"function_name": "run"},
        dependency_solution_id=dependency_solution_id,
    )
    resolution = DeploymentResolutionMap(workflows={workflow.portable_ref: workflow})
    resolution_hash = sha256_digest(canonical_json(resolution))
    manifest = CompiledDeploymentManifest(
        solution_id=uuid4(),
        deployment_id=uuid4(),
        bundle_hash="sha256:bundle",
        resolution_map_hash=resolution_hash,
        source=DeploymentSource(artifact_key="source.zip", runtime_prefix="runtime/"),
        workflows={workflow.portable_ref: workflow},
    )

    with pytest.raises(ValueError, match="undeclared dependency owner"):
        validate_runtime_closure(
            manifest,
            resolution,
            [],
            expected_manifest_hash=manifest.content_hash(),
            expected_resolution_hash=resolution_hash,
        )
