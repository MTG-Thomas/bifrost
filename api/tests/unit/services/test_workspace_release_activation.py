"""Deterministic invariants for atomic immutable Workspace activation."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bifrost.workspace_release import canonical_digest, workspace_manifest_id
from src.services.workspace_release_activation import (
    PREPARED_EVIDENCE_SCHEMA,
    WorkspaceReleaseActivationError,
    WorkspaceReleaseActivationService,
    projection_paths_id,
    registration_state_fingerprint,
    release_status,
    validate_prepared_release_evidence,
)
from src.services.workspace_release_runtime import WorkspaceReleaseDescriptor


def _prepared_rows():
    organization_id = uuid4()
    artifact_id = uuid4()
    release_row_id = uuid4()
    now = datetime.now(timezone.utc)
    path = "workflows/demo.py"
    source_hash = "a" * 64
    release_id = "sha256:" + "b" * 64
    manifest_id = workspace_manifest_id({path: source_hash})
    prefix = (
        f"_workspace_releases/{organization_id}/"
        f"{release_id.removeprefix('sha256:')}/files/"
    )
    artifact = SimpleNamespace(
        id=artifact_id,
        organization_id=organization_id,
        candidate_id="sha256:" + "c" * 64,
        content_id="sha256:" + "d" * 64,
        closure_id="sha256:" + "e" * 64,
        release_id=release_id,
        base_release_id="repo-v1:" + "f" * 64,
        base_manifest_id=workspace_manifest_id({}),
        effective_manifest_id=manifest_id,
        manifest={
            "entry": {"path": path, "function": "demo"},
            "closure": [{"path": path, "sha256": source_hash}],
            "release_id": release_id,
            "base_release_id": "repo-v1:" + "f" * 64,
        },
    )
    descriptor = WorkspaceReleaseDescriptor(
        release_row_id=release_row_id,
        artifact_id=artifact_id,
        organization_id=organization_id,
        release_id=release_id,
        effective_manifest_id=manifest_id,
        runtime_storage_prefix=prefix,
        source_hashes={path: source_hash},
        effective_registrations={},
        effective_registration_manifest_id="sha256:" + "2" * 64,
        source_commit_sha="3" * 40,
        source_tree_sha="4" * 40,
        registration_state_fingerprint="sha256:" + "5" * 64,
    )
    evidence = {
        "schema_version": PREPARED_EVIDENCE_SCHEMA,
        "artifact_id": str(artifact_id),
        "candidate_id": artifact.candidate_id,
        "content_id": artifact.content_id,
        "release_id": release_id,
        "base_release_id": artifact.base_release_id,
        "base_manifest_id": artifact.base_manifest_id,
        "effective_manifest_id": manifest_id,
        "effective_files": {path: source_hash},
        "runtime_storage_prefix": prefix,
        "file_count": 1,
        "total_bytes": 10,
        "compile": {"succeeded": True, "file_count": 1},
        "import_smoke": {
            "entry_path": path,
            "entry_function": "demo",
            "imported": True,
            "function_callable": True,
            "source": "immutable_candidate_tree",
        },
        "projection_paths": [
            {
                "path": path,
                "base_sha256": None,
                "target_sha256": source_hash,
            }
        ],
        "prepared_at": now.isoformat(),
    }
    evidence["evidence_id"] = canonical_digest(evidence)
    release = SimpleNamespace(
        id=release_row_id,
        artifact_id=artifact_id,
        organization_id=organization_id,
        activation_state="prepared",
        lock_state="not_queued",
        lock_in_job_id=None,
        previous_release_id=None,
        prepared_evidence=evidence,
        prepared_at=now,
        activation_evidence=None,
    )
    return release, artifact, descriptor


def test_prepared_evidence_binds_exact_immutable_and_projection_manifests() -> None:
    release, artifact, descriptor = _prepared_rows()

    evidence = validate_prepared_release_evidence(release, artifact, descriptor)

    assert evidence["release_id"] == descriptor.release_id
    assert projection_paths_id(evidence["projection_paths"]).startswith("sha256:")


def test_prepared_projection_member_from_wrong_release_fails_closed() -> None:
    release, artifact, descriptor = _prepared_rows()
    release.prepared_evidence = {
        **release.prepared_evidence,
        "projection_paths": [
            {
                **release.prepared_evidence["projection_paths"][0],
                "target_sha256": "9" * 64,
            }
        ],
    }
    evidence_without_id = dict(release.prepared_evidence)
    evidence_without_id.pop("evidence_id")
    release.prepared_evidence["evidence_id"] = canonical_digest(evidence_without_id)

    with pytest.raises(WorkspaceReleaseActivationError, match="projection path hash"):
        validate_prepared_release_evidence(release, artifact, descriptor)


def test_prepared_projection_must_reconstruct_exact_base_manifest() -> None:
    release, artifact, descriptor = _prepared_rows()
    release.prepared_evidence = {
        **release.prepared_evidence,
        "projection_paths": [
            {
                **release.prepared_evidence["projection_paths"][0],
                "base_sha256": "8" * 64,
            }
        ],
    }
    evidence_without_id = dict(release.prepared_evidence)
    evidence_without_id.pop("evidence_id")
    release.prepared_evidence["evidence_id"] = canonical_digest(evidence_without_id)

    with pytest.raises(WorkspaceReleaseActivationError, match="base manifest"):
        validate_prepared_release_evidence(release, artifact, descriptor)


def test_live_status_separates_runtime_coherence_from_pending_history() -> None:
    release, artifact, _ = _prepared_rows()
    release.activation_state = "live"
    release.activation_evidence = {
        "activated_at": release.prepared_at.isoformat(),
        "canary": {"execution_id": str(uuid4())},
    }

    status = release_status(release, artifact)

    assert status.is_live is True
    assert status.runtime.state == "coherent"
    assert status.history.state == "pending"


def test_registration_fingerprint_captures_activation_surface() -> None:
    role = SimpleNamespace(id=uuid4())
    workflow = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        type="workflow",
        is_active=True,
        endpoint_enabled=False,
        public_endpoint=False,
        api_key_enabled=False,
        access_level="role_based",
        roles=[role],
    )

    before = registration_state_fingerprint(workflow)
    workflow.endpoint_enabled = True

    assert registration_state_fingerprint(workflow) != before


@pytest.mark.asyncio
async def test_translated_activation_failure_rolls_back_partial_transaction() -> None:
    db = SimpleNamespace(rollback=AsyncMock())
    service = WorkspaceReleaseActivationService(db, uuid4())
    service._activate_locked = AsyncMock(  # type: ignore[method-assign]
        side_effect=WorkspaceReleaseActivationError("stale CAS")
    )

    with pytest.raises(WorkspaceReleaseActivationError, match="stale CAS"):
        await service.activate(uuid4(), SimpleNamespace())

    db.rollback.assert_awaited_once()
