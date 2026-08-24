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
    WorkspaceDraftCanaryService,
    build_draft_runtime_evidence,
    draft_canary_attestation,
    extract_verified_draft_source,
    verify_draft_runtime_evidence,
    workflow_data_from_draft_evidence,
)
from src.services.workspace_promotions import UNDECLARED_EFFECT, _canonical_candidate
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
    manifest = {
        "entry": entry,
        "closure": [{"path": path, "sha256": digest}],
        "declared_effects": effect_list,
        "computed_effects": effect_list,
        "risk_class": "R0",
        "diagnostics": [],
        "bounds": {
            "max_duration_seconds": 15,
            "max_output_bytes": 128,
        },
        "effective_registrations": {},
        "protected_source": {"commit_sha": "2" * 40, "tree_sha": "3" * 40},
    }
    return SimpleNamespace(
        id=artifact_id,
        organization_id=organization_id,
        candidate_id=_canonical_candidate(manifest),
        content_id=content_id,
        closure_id=closure_id,
        release_id="sha256:" + "4" * 64,
        base_release_id="repo-v1:" + "5" * 64,
        effective_manifest_id="sha256:" + "6" * 64,
        source_revision="2" * 40,
        source_tree_sha="3" * 40,
        schema_version="bifrost.workspace-promotion-bundle/v2",
        target_kind="workspace",
        source_artifact_key=(
            f"_workspace_promotion_artifacts/{organization_id}/"
            f"{content_id.removeprefix('sha256:')}/source.zip"
        ),
        artifact_state="review_required",
        risk_class="R0",
        manifest=manifest,
    ), {path: source}


def test_server_canary_rejects_local_only_draft() -> None:
    artifact, _ = _artifact()
    artifact.target_kind = "draft"
    artifact.schema_version = "bifrost.workspace-draft-upload/v1"
    artifact.source_revision = None
    artifact.source_tree_sha = None

    with pytest.raises(WorkspaceDraftCanaryError, match="local-only drafts"):
        build_draft_runtime_evidence(
            artifact,
            workspace_draft_runtime_prefix(
                artifact.organization_id, artifact.content_id
            ),
        )


def test_archive_must_match_every_immutable_closure_byte() -> None:
    artifact, files = _artifact()
    evidence = build_draft_runtime_evidence(
        artifact,
        workspace_draft_runtime_prefix(artifact.organization_id, artifact.content_id),
    )

    assert (
        extract_verified_draft_source(_zip(files), evidence["source_hashes"]) == files
    )

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

    with pytest.raises(WorkspaceDraftCanaryError, match="exact R0|not canonical"):
        build_draft_runtime_evidence(artifact, "_workspace_releases/o/d/x/files/")


def test_server_canary_rejects_r1_r2_before_runtime_materialization() -> None:
    artifact, _ = _artifact(effects=["integration.read:microsoft_graph"])
    artifact.risk_class = "R1"
    artifact.manifest["risk_class"] = "R1"
    artifact.candidate_id = _canonical_candidate(artifact.manifest)

    with pytest.raises(WorkspaceDraftCanaryError, match="exact R0"):
        build_draft_runtime_evidence(
            artifact,
            workspace_draft_runtime_prefix(
                artifact.organization_id, artifact.content_id
            ),
        )


def test_undeclared_r2_cannot_enter_reviewed_canary() -> None:
    artifact, _ = _artifact(effects=[UNDECLARED_EFFECT])
    artifact.risk_class = "R2"
    artifact.manifest["risk_class"] = "R2"
    artifact.manifest["declared_effects"] = []
    artifact.candidate_id = _canonical_candidate(artifact.manifest)

    with pytest.raises(WorkspaceDraftCanaryError, match="exact R0"):
        build_draft_runtime_evidence(
            artifact,
            workspace_draft_runtime_prefix(
                artifact.organization_id, artifact.content_id
            ),
        )


def test_local_only_draft_artifact_is_never_canary_eligible() -> None:
    artifact, _ = _artifact()
    artifact.target_kind = "draft"
    artifact.schema_version = "bifrost.workspace-draft-upload/v1"

    with pytest.raises(WorkspaceDraftCanaryError, match="reviewed protected-main"):
        build_draft_runtime_evidence(
            artifact,
            workspace_draft_runtime_prefix(
                artifact.organization_id, artifact.content_id
            ),
        )


@pytest.mark.asyncio
async def test_canary_service_rejects_local_upload_before_execution() -> None:
    artifact, _ = _artifact()
    artifact.target_kind = "draft"
    artifact.schema_version = "bifrost.workspace-draft-upload/v1"

    class Database:
        async def scalar(self, _statement):
            return artifact

    service = WorkspaceDraftCanaryService(Database(), artifact.organization_id)

    with pytest.raises(WorkspaceDraftCanaryError, match="only reviewed"):
        await service._artifact(artifact.id)


def test_queue_and_durable_draft_evidence_must_be_identical() -> None:
    artifact, _ = _artifact()
    evidence = build_draft_runtime_evidence(
        artifact,
        workspace_draft_runtime_prefix(artifact.organization_id, artifact.content_id),
    )
    evidence_hash = sha256_digest(canonical_json(evidence))

    assert (
        verify_draft_runtime_evidence(evidence, evidence, evidence_hash, artifact)
        == evidence
    )

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
    assert (
        execution_timeout_from_context(
            {
                "runtime_mode": "workspace-canary-v1",
                "timeout_seconds": 30,
                "draft_max_duration_seconds": 7,
            },
            300,
        )
        == 7
    )

    with pytest.raises(WorkspaceDraftDurationLimitInvalid, match="hard duration"):
        execution_timeout_from_context(
            {"runtime_mode": "workspace-canary-v1", "timeout_seconds": 30}, 300
        )

    assert (
        execution_timeout_from_context(
            {
                "runtime_mode": "workspace-release-v1",
                "timeout_seconds": 30,
                "runtime_max_duration_seconds": 5,
            },
            300,
        )
        == 5
    )


def test_worker_rejects_oversize_serialized_draft_output() -> None:
    context = {
        "runtime_mode": "workspace-canary-v1",
        "draft_max_output_bytes": 10,
    }

    enforce_draft_output_limit(context, {"a": 1})
    with pytest.raises(WorkspaceDraftOutputLimitExceeded, match="limit is 10"):
        enforce_draft_output_limit(context, {"message": "too large"})

    release_context = {
        "runtime_mode": "workspace-release-v1",
        "runtime_max_output_bytes": 10,
    }
    with pytest.raises(WorkspaceDraftOutputLimitExceeded, match="limit is 10"):
        enforce_draft_output_limit(release_context, {"message": "too large"})

    # Legacy execution remains unaffected.
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
        runtime_mode="workspace-canary-v1",
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
