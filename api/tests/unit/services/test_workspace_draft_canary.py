"""Deterministic safety tests for server-issued Workspace draft canaries."""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bifrost.workspace_release import workspace_closure_id, workspace_content_id
from src.models.enums import ExecutionStatus
from src.services.solutions.deployment_manifest import canonical_json, sha256_digest
from src.services.workspace_draft_canary import (
    WorkspaceDraftCanaryError,
    build_draft_runtime_evidence,
    draft_canary_attestation,
    extract_verified_draft_source,
    verify_draft_runtime_evidence,
    workflow_data_from_draft_evidence,
)
from src.services.workspace_promotion_storage import workspace_draft_runtime_prefix
from src.services.execution.process_pool import (
    WorkspaceDraftDurationLimitInvalid,
    execution_timeout_from_context,
)
from src.services.execution.draft_limits import (
    WorkspaceDraftOutputLimitExceeded,
    enforce_draft_output_limit,
)


def _zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return output.getvalue()


def _artifact(*, effects: list[str] | None = None):
    path = "workflows/read_only.py"
    source = b"def inspect_workspace():\n    return {'ok': True}\n"
    digest = hashlib.sha256(source).hexdigest()
    organization_id = uuid4()
    artifact_id = uuid4()
    effect_list = ["bifrost.read"] if effects is None else effects
    entry = {"path": path, "function": "inspect_workspace"}
    closure_id = workspace_closure_id(entry, {path: digest})
    content_id = workspace_content_id(entry, closure_id)
    return SimpleNamespace(
        id=artifact_id,
        organization_id=organization_id,
        candidate_id="sha256:" + "1" * 64,
        content_id=content_id,
        closure_id=closure_id,
        source_artifact_key=(
            f"_workspace_promotion_artifacts/{organization_id}/"
            f"{content_id.removeprefix('sha256:')}/source.zip"
        ),
        artifact_state="review_required",
        manifest={
            "entry": entry,
            "closure": [{"path": path, "sha256": digest}],
            "declared_effects": effect_list,
            "computed_effects": effect_list,
            "bounds": {
                "max_duration_seconds": 15,
                "max_output_bytes": 128,
            },
            "effective_registrations": {},
        },
    ), {path: source}


def test_archive_must_match_every_immutable_closure_byte() -> None:
    artifact, files = _artifact()
    evidence = build_draft_runtime_evidence(
        artifact,
        workspace_draft_runtime_prefix(artifact.organization_id, artifact.content_id),
    )

    assert extract_verified_draft_source(
        _zip(files), evidence["source_hashes"]
    ) == files

    stale = {next(iter(files)): b"stale\n"}
    with pytest.raises(WorkspaceDraftCanaryError, match="integrity mismatch"):
        extract_verified_draft_source(_zip(stale), evidence["source_hashes"])


@pytest.mark.parametrize(
    "effects",
    [
        ["integration.read:microsoft_graph"],
        ["bifrost.read", "network.unknown"],
        ["bifrost.write"],
        [],
    ],
)
def test_first_canary_policy_allows_only_bifrost_read(effects: list[str]) -> None:
    artifact, _ = _artifact(effects=effects)

    with pytest.raises(WorkspaceDraftCanaryError, match="exactly bifrost.read"):
        build_draft_runtime_evidence(artifact, "_workspace_releases/o/d/x/files/")


def test_queue_and_durable_draft_evidence_must_be_identical() -> None:
    artifact, _ = _artifact()
    evidence = build_draft_runtime_evidence(
        artifact,
        workspace_draft_runtime_prefix(artifact.organization_id, artifact.content_id),
    )
    evidence_hash = sha256_digest(canonical_json(evidence))

    assert verify_draft_runtime_evidence(
        evidence, evidence, evidence_hash, artifact
    ) == evidence

    tampered = {**evidence, "source_hashes": {"workflows/read_only.py": "f" * 64}}
    with pytest.raises(WorkspaceDraftCanaryError, match="pins differ"):
        verify_draft_runtime_evidence(tampered, evidence, evidence_hash, artifact)


def test_draft_workflow_data_is_derived_only_from_pin() -> None:
    artifact, _ = _artifact()
    evidence = build_draft_runtime_evidence(
        artifact,
        workspace_draft_runtime_prefix(artifact.organization_id, artifact.content_id),
    )

    data = workflow_data_from_draft_evidence(evidence)

    assert data["path"] == "workflows/read_only.py"
    assert data["timeout_seconds"] == 15
    assert data["max_output_bytes"] == 128
    assert data["runtime_storage_prefix"] == evidence["runtime_storage_prefix"]


def test_process_pool_enforces_draft_deadline_from_immutable_context() -> None:
    assert execution_timeout_from_context(
        {
            "runtime_mode": "workspace-draft-v1",
            "timeout_seconds": 30,
            "draft_max_duration_seconds": 7,
        },
        300,
    ) == 7

    with pytest.raises(WorkspaceDraftDurationLimitInvalid, match="hard duration"):
        execution_timeout_from_context(
            {"runtime_mode": "workspace-draft-v1", "timeout_seconds": 30}, 300
        )


def test_worker_rejects_oversize_serialized_draft_output() -> None:
    context = {
        "runtime_mode": "workspace-draft-v1",
        "draft_max_output_bytes": 10,
    }

    enforce_draft_output_limit(context, {"a": 1})
    with pytest.raises(WorkspaceDraftOutputLimitExceeded, match="limit is 10"):
        enforce_draft_output_limit(context, {"message": "too large"})

    # The new bound is intentionally isolated from registered/legacy execution.
    enforce_draft_output_limit({"runtime_mode": "repo-v1"}, {"x": "x" * 1000})


def test_activation_attestation_requires_successful_exact_execution_pin() -> None:
    artifact, _ = _artifact()
    evidence = build_draft_runtime_evidence(
        artifact,
        workspace_draft_runtime_prefix(artifact.organization_id, artifact.content_id),
    )
    execution = SimpleNamespace(
        id=uuid4(),
        status=ExecutionStatus.SUCCESS,
        completed_at=datetime.now(timezone.utc),
        duration_ms=12,
        runtime_mode="workspace-draft-v1",
        workflow_id=None,
        organization_id=artifact.organization_id,
        runtime_evidence=evidence,
        runtime_evidence_hash=sha256_digest(canonical_json(evidence)),
    )

    attestation = draft_canary_attestation(execution, artifact)

    assert attestation["execution_id"] == str(execution.id)
    assert attestation["content_id"] == artifact.content_id
    assert attestation["runtime_evidence_hash"] == execution.runtime_evidence_hash

    execution.runtime_evidence = {**evidence, "content_id": "sha256:" + "f" * 64}
    with pytest.raises(WorkspaceDraftCanaryError, match="hash is invalid"):
        draft_canary_attestation(execution, artifact)
