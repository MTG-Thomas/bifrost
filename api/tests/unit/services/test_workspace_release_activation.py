"""Deterministic invariants for atomic immutable Workspace activation."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bifrost.workspace_release import canonical_digest, workspace_manifest_id
from bifrost.promotion import sha256_bytes
from bifrost.workspace_release_authorization import (
    computed_effects_id,
    risk_acknowledgement,
)
from src.models.contracts.workspace_promotions import (
    WorkspaceReleaseRiskAcknowledgement,
    WorkspaceReviewedCanaryAuthorization,
    WorkspaceRiskAcknowledgementAuthorization,
)
from src.services import workspace_release_activation as activation_module
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
from src.services.workspace_release_materialization import (
    prepared_activation_challenge,
)


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
        effective_registration_manifest_id="sha256:" + "2" * 64,
        source_revision="3" * 40,
        source_tree_sha="4" * 40,
        risk_class="R0",
        policy_version="workspace-promotion-v2",
        manifest={
            "entry": {"path": path, "function": "demo"},
            "closure": [{"path": path, "sha256": source_hash}],
            "release_id": release_id,
            "base_release_id": "repo-v1:" + "f" * 64,
            "effective_registration_manifest_id": "sha256:" + "2" * 64,
            "risk_class": "R0",
            "computed_effects": ["bifrost.read"],
            "protected_source": {
                "commit_sha": "3" * 40,
                "tree_sha": "4" * 40,
            },
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
        governed_paths=(path,),
        governed_manifest_id=workspace_manifest_id({path: source_hash}),
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
        "governed_paths": [path],
        "governed_manifest_id": workspace_manifest_id({path: source_hash}),
        "effective_registration_manifest_id": "sha256:" + "2" * 64,
        "risk_class": "R0",
        "policy_version": "workspace-promotion-v2",
        "computed_effects": ["bifrost.read"],
        "computed_effects_id": canonical_digest(
            {
                "schema_version": "bifrost.workspace-computed-effects/v1",
                "effects": ["bifrost.read"],
            }
        ),
        "protected_source": {
            "commit_sha": "3" * 40,
            "tree_sha": "4" * 40,
        },
        "runtime_storage_prefix": prefix,
        "file_count": 1,
        "total_bytes": 10,
        "compile": {"succeeded": True, "file_count": 1},
        "import_validation": {
            "state": "succeeded",
            "selected": {
                "entry_path": path,
                "entry_function": "demo",
                "imported": True,
                "function_callable": True,
                "source": "immutable_candidate_tree",
                "entity_type": "workflow",
                "relation": "selected_entry",
            },
            "targets": [
                {
                    "entry_path": path,
                    "entry_function": "demo",
                    "imported": True,
                    "function_callable": True,
                    "source": "immutable_candidate_tree",
                    "entity_type": "workflow",
                    "relation": "selected_entry",
                }
            ],
        },
        "effect_execution": "reviewed_canary_required",
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
        error_code=None,
        error_message=None,
    )
    return release, artifact, descriptor


def _as_risk_release(
    release, artifact, risk_class: str, effects: list[str]
) -> None:
    artifact.risk_class = risk_class
    artifact.manifest["risk_class"] = risk_class
    artifact.manifest["computed_effects"] = effects
    evidence = dict(release.prepared_evidence)
    evidence.update(
        {
            "risk_class": risk_class,
            "computed_effects": effects,
            "computed_effects_id": computed_effects_id(effects),
            "import_validation": {
                "state": "not_performed",
                "reason": "non_r0_source_is_not_executed_during_prepare",
            },
            "effect_execution": "not_performed",
        }
    )
    evidence.pop("evidence_id")
    evidence["evidence_id"] = canonical_digest(evidence)
    release.prepared_evidence = evidence


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
        "authorization": {
            "kind": "reviewed_canary",
            "authorization_id": "sha256:" + "6" * 64,
            "canary_attestation": {"execution_id": str(uuid4())},
        },
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
async def test_r0_authorization_binds_exact_reviewed_canary_and_actor() -> None:
    release, artifact, _descriptor = _prepared_rows()
    challenge = prepared_activation_challenge(release.prepared_evidence)
    canary_id = uuid4()
    request = SimpleNamespace(
        authorization=WorkspaceReviewedCanaryAuthorization(
            kind="reviewed_canary",
            challenge_id=challenge["challenge_id"],
            canary_execution_id=canary_id,
        )
    )
    attestation = {
        "execution_id": str(canary_id),
        "candidate_id": artifact.candidate_id,
    }
    service = WorkspaceReleaseActivationService(SimpleNamespace(), uuid4())
    service._canary_attestation = AsyncMock(return_value=attestation)  # type: ignore[method-assign]
    actor_id = uuid4()

    evidence = await service._authorization_evidence(
        request,
        artifact,
        release.prepared_evidence,
        authorized_by_user_id=actor_id,
        authorized_at=release.prepared_at,
    )

    assert evidence["kind"] == "reviewed_canary"
    assert evidence["challenge_id"] == challenge["challenge_id"]
    assert evidence["canary_attestation"] == attestation
    assert evidence["authorized_by_user_id"] == str(actor_id)
    without_id = dict(evidence)
    authorization_id = without_id.pop("authorization_id")
    assert authorization_id == canonical_digest(without_id)


@pytest.mark.asyncio
async def test_r2_authorization_requires_exact_nonexecution_acknowledgement() -> None:
    release, artifact, _descriptor = _prepared_rows()
    effects = ["integration.write:halopsa"]
    _as_risk_release(release, artifact, "R2", effects)
    challenge = prepared_activation_challenge(release.prepared_evidence)
    acknowledgement = risk_acknowledgement(challenge)
    request = SimpleNamespace(
        authorization=WorkspaceRiskAcknowledgementAuthorization(
            kind="risk_acknowledgement",
            acknowledgement=WorkspaceReleaseRiskAcknowledgement.model_validate(
                acknowledgement
            ),
        )
    )
    service = WorkspaceReleaseActivationService(SimpleNamespace(), uuid4())
    service._canary_attestation = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("R2 source must not execute as a canary")
    )

    evidence = await service._authorization_evidence(
        request,
        artifact,
        release.prepared_evidence,
        authorized_by_user_id=uuid4(),
        authorized_at=release.prepared_at,
    )

    assert evidence["kind"] == "risk_acknowledgement"
    assert evidence["acknowledgement"] == acknowledgement
    service._canary_attestation.assert_not_awaited()


@pytest.mark.asyncio
async def test_r2_rejects_canary_authorization_before_execution() -> None:
    release, artifact, _descriptor = _prepared_rows()
    _as_risk_release(
        release, artifact, "R2", ["integration.write:halopsa"]
    )
    challenge = prepared_activation_challenge(release.prepared_evidence)
    request = SimpleNamespace(
        authorization=WorkspaceReviewedCanaryAuthorization(
            kind="reviewed_canary",
            challenge_id=challenge["challenge_id"],
            canary_execution_id=uuid4(),
        )
    )
    service = WorkspaceReleaseActivationService(SimpleNamespace(), uuid4())
    service._canary_attestation = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("R2 source must not execute as a canary")
    )

    with pytest.raises(
        WorkspaceReleaseActivationError,
        match="explicit risk acknowledgement",
    ):
        await service._authorization_evidence(
            request,
            artifact,
            release.prepared_evidence,
            authorized_by_user_id=uuid4(),
            authorized_at=release.prepared_at,
        )

    service._canary_attestation.assert_not_awaited()


@pytest.mark.asyncio
async def test_activation_commits_live_pointer_and_projection_job_atomically(
    monkeypatch,
) -> None:
    release, artifact, descriptor = _prepared_rows()
    events: list[str] = []

    class Database:
        rollback = AsyncMock()

        async def flush(self):
            events.append("flush")

        async def commit(self):
            events.append("commit")

        async def refresh(self, _row):
            return None

    service = WorkspaceReleaseActivationService(Database(), release.organization_id)
    service._release_rows = AsyncMock(return_value=(release, artifact))  # type: ignore[method-assign]
    service._current_live_any_organization = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service._validate_artifact_lifecycle = AsyncMock()  # type: ignore[method-assign]
    service._validate_base_cas = AsyncMock()  # type: ignore[method-assign]
    service._authorization_evidence = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "schema_version": "bifrost.workspace-release-authorization/v1",
            "kind": "reviewed_canary",
            "challenge_id": "sha256:" + "7" * 64,
            "authorization_id": "sha256:" + "8" * 64,
            "canary_attestation": {"execution_id": str(uuid4())},
        }
    )
    service._apply_registration = AsyncMock(return_value=[])  # type: ignore[method-assign]
    monkeypatch.setattr(
        activation_module.WorkspaceReleaseDescriptor,
        "from_rows",
        lambda *_args: descriptor,
    )
    monkeypatch.setattr(
        activation_module,
        "acquire_workspace_release_lock",
        AsyncMock(),
    )
    projection_job = SimpleNamespace(id=uuid4())

    async def enqueue(_db, *, release, **_kwargs):
        events.append("projection_job_staged")
        release.lock_state = "queued"
        release.lock_in_job_id = projection_job.id
        return projection_job, False

    monkeypatch.setattr(
        "src.jobs.platform.workspace_release_lock.enqueue_workspace_release_lock",
        enqueue,
    )

    async def audit(*_args, **_kwargs):
        events.append("strict_audit_staged")

    monkeypatch.setattr(activation_module, "emit_audit", audit)
    request = SimpleNamespace(
        artifact_id=artifact.id,
        candidate_id=artifact.candidate_id,
        workspace_release_id=artifact.release_id,
        expected_base_release_id=artifact.base_release_id,
        expected_active_release_id=None,
        prepared_evidence_id=release.prepared_evidence["evidence_id"],
        authorization=SimpleNamespace(kind="reviewed_canary"),
    )

    status = await service.activate(
        release.id,
        request,
        authorized_by_user_id=uuid4(),
        authorized_by_email="operator@example.test",
        authorized_by_name="Operator",
    )

    assert events.index("projection_job_staged") < events.index("commit")
    assert events.index("strict_audit_staged") < events.index("commit")
    assert release.activation_state == "live"
    assert release.lock_state == "queued"
    assert release.lock_in_job_id == projection_job.id
    assert status.is_live is True
    assert status.history.job_id == projection_job.id


@pytest.mark.asyncio
async def test_projection_job_failure_rolls_back_live_activation(
    monkeypatch,
) -> None:
    release, artifact, descriptor = _prepared_rows()
    db = SimpleNamespace(
        flush=AsyncMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
        rollback=AsyncMock(),
    )
    service = WorkspaceReleaseActivationService(db, release.organization_id)
    service._release_rows = AsyncMock(return_value=(release, artifact))  # type: ignore[method-assign]
    service._current_live_any_organization = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service._validate_artifact_lifecycle = AsyncMock()  # type: ignore[method-assign]
    service._validate_base_cas = AsyncMock()  # type: ignore[method-assign]
    service._authorization_evidence = AsyncMock(return_value={})  # type: ignore[method-assign]
    service._apply_registration = AsyncMock(return_value=[])  # type: ignore[method-assign]
    monkeypatch.setattr(
        activation_module.WorkspaceReleaseDescriptor,
        "from_rows",
        lambda *_args: descriptor,
    )
    monkeypatch.setattr(
        activation_module,
        "acquire_workspace_release_lock",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.workspace_release_lock.enqueue_workspace_release_lock",
        AsyncMock(side_effect=RuntimeError("job insert failed")),
    )
    request = SimpleNamespace(
        artifact_id=artifact.id,
        candidate_id=artifact.candidate_id,
        workspace_release_id=artifact.release_id,
        expected_base_release_id=artifact.base_release_id,
        expected_active_release_id=None,
        prepared_evidence_id=release.prepared_evidence["evidence_id"],
        authorization=SimpleNamespace(kind="reviewed_canary"),
    )

    with pytest.raises(RuntimeError, match="job insert failed"):
        await service.activate(
            release.id,
            request,
            authorized_by_user_id=uuid4(),
            authorized_by_email="operator@example.test",
            authorized_by_name="Operator",
        )

    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_next_activation_waits_for_current_live_history_lock() -> None:
    request = SimpleNamespace(expected_active_release_id="sha256:" + "1" * 64)
    artifact = SimpleNamespace(
        base_release_id="sha256:" + "1" * 64,
        base_manifest_id="sha256:" + "2" * 64,
    )
    current = (
        SimpleNamespace(lock_state="attention_required"),
        SimpleNamespace(),
    )

    with pytest.raises(WorkspaceReleaseActivationError, match="history-locked"):
        await WorkspaceReleaseActivationService._validate_base_cas(
            SimpleNamespace(),
            request,
            artifact,
            current,
        )


@pytest.mark.asyncio
async def test_activation_rebuilds_hybrid_base_under_lock(monkeypatch) -> None:
    governed_path = "modules/shared.py"
    ungoverned_path = "workflows/legacy.py"
    immutable_source = b"VALUE = 'reviewed'\n"
    stale_repo_source = b"VALUE = 'stale'\n"
    current_legacy_source = b"VALUE = 'current'\n"
    release_id = "sha256:" + "1" * 64
    governed_hash = sha256_bytes(immutable_source)
    descriptor = SimpleNamespace(
        release_id=release_id,
        runtime_storage_prefix="_workspace_releases/org/release/files/",
        governed_paths=(governed_path,),
        governed_source_hashes={governed_path: governed_hash},
    )
    hybrid_hashes = {
        governed_path: governed_hash,
        ungoverned_path: sha256_bytes(current_legacy_source),
    }
    artifact = SimpleNamespace(
        base_release_id=release_id,
        base_manifest_id=workspace_manifest_id(hybrid_hashes),
    )
    request = SimpleNamespace(expected_active_release_id=release_id)
    current = (SimpleNamespace(lock_state="locked"), SimpleNamespace())

    monkeypatch.setattr(
        activation_module,
        "read_generation_stable_executable_snapshot",
        AsyncMock(
            return_value={
                governed_path: stale_repo_source,
                ungoverned_path: current_legacy_source,
            }
        ),
    )
    monkeypatch.setattr(
        activation_module.WorkspaceReleaseDescriptor,
        "from_rows",
        lambda *_args: descriptor,
    )
    storage = SimpleNamespace(
        read_many=AsyncMock(return_value={governed_path: immutable_source})
    )
    monkeypatch.setattr(
        activation_module,
        "WorkspaceReleaseStorage",
        lambda _prefix: storage,
    )

    await WorkspaceReleaseActivationService(
        SimpleNamespace(), uuid4()
    )._validate_base_cas(request, artifact, current)

    storage.read_many.assert_awaited_once_with([governed_path])


@pytest.mark.asyncio
async def test_activation_rejects_changed_ungoverned_hybrid_base(monkeypatch) -> None:
    governed_path = "modules/shared.py"
    ungoverned_path = "workflows/legacy.py"
    immutable_source = b"VALUE = 'reviewed'\n"
    release_id = "sha256:" + "1" * 64
    descriptor = SimpleNamespace(
        release_id=release_id,
        runtime_storage_prefix="_workspace_releases/org/release/files/",
        governed_paths=(governed_path,),
        governed_source_hashes={governed_path: sha256_bytes(immutable_source)},
    )
    artifact = SimpleNamespace(
        base_release_id=release_id,
        base_manifest_id=workspace_manifest_id(
            {
                governed_path: sha256_bytes(immutable_source),
                ungoverned_path: sha256_bytes(b"VALUE = 'before'\n"),
            }
        ),
    )
    request = SimpleNamespace(expected_active_release_id=release_id)
    current = (SimpleNamespace(lock_state="locked"), SimpleNamespace())
    monkeypatch.setattr(
        activation_module,
        "read_generation_stable_executable_snapshot",
        AsyncMock(
            return_value={
                governed_path: b"VALUE = 'stale'\n",
                ungoverned_path: b"VALUE = 'changed after preview'\n",
            }
        ),
    )
    monkeypatch.setattr(
        activation_module.WorkspaceReleaseDescriptor,
        "from_rows",
        lambda *_args: descriptor,
    )
    monkeypatch.setattr(
        activation_module,
        "WorkspaceReleaseStorage",
        lambda _prefix: SimpleNamespace(
            read_many=AsyncMock(return_value={governed_path: immutable_source})
        ),
    )

    with pytest.raises(
        WorkspaceReleaseActivationError,
        match="Live Workspace base changed after preview",
    ):
        await WorkspaceReleaseActivationService(
            SimpleNamespace(), uuid4()
        )._validate_base_cas(request, artifact, current)


@pytest.mark.asyncio
async def test_translated_activation_failure_rolls_back_partial_transaction() -> None:
    db = SimpleNamespace(rollback=AsyncMock())
    service = WorkspaceReleaseActivationService(db, uuid4())
    service._activate_locked = AsyncMock(  # type: ignore[method-assign]
        side_effect=WorkspaceReleaseActivationError("stale CAS")
    )

    with pytest.raises(WorkspaceReleaseActivationError, match="stale CAS"):
        await service.activate(
            uuid4(),
            SimpleNamespace(),
            authorized_by_user_id=uuid4(),
            authorized_by_email="operator@example.test",
            authorized_by_name="Operator",
        )

    db.rollback.assert_awaited_once()
