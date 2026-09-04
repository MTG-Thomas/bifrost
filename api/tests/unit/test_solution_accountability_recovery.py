"""Recovery must prove the original deployment before touching evidence."""

import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.solutions.accountability_recovery import (
    AccountabilityRecoveryConflict,
    recover_solution_deploy_accountability,
    validate_recovery_deployment,
)


def deployment():
    solution_id, job_id, org_id = uuid4(), uuid4(), uuid4()
    digest = hashlib.sha256(b"artifact").hexdigest()
    result = {"solution_id": str(solution_id), "candidate_id": f"sha256:{digest}"}
    solution = SimpleNamespace(id=solution_id, slug="test", organization_id=org_id)
    job = SimpleNamespace(
        job_type="solution.deploy",
        status="succeeded",
        organization_id=org_id,
        encrypted_payload=None,
        result=result.copy(),
        payload={
            "deploy_job_id": str(job_id),
            "kind": "deploy",
            "install_id": str(solution_id),
            "input_sha256": digest,
            "options": {
                "candidate_id": f"sha256:{digest}",
                "accountability_organization_id": str(org_id),
            },
        },
    )
    projection = SimpleNamespace(
        status="succeeded",
        install_id=solution_id,
        result=result.copy(),
        created_at=datetime.now(timezone.utc),
    )
    db = AsyncMock()
    db.scalar.return_value = None
    db.get.side_effect = [solution, job, projection]
    return db, solution, job, projection, job_id


@pytest.mark.asyncio
async def test_validates_exact_successful_deployment():
    db, solution, job, _, job_id = deployment()
    actual, payload, org = await validate_recovery_deployment(
        db, solution_id=solution.id, deploy_job_id=job_id
    )
    assert actual is solution
    assert payload.input_sha256 == job.payload["input_sha256"]
    assert org == solution.organization_id
    db.flush.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "failed_job",
        "failed_projection",
        "other_solution",
        "other_org",
        "other_candidate",
        "missing_org",
        "result_disagrees",
    ],
)
async def test_rejects_unproven_deployment(change):
    db, solution, job, projection, job_id = deployment()
    if change == "failed_job":
        job.status = "failed"
    elif change == "failed_projection":
        projection.status = "failed"
    elif change == "other_solution":
        projection.install_id = uuid4()
    elif change == "other_org":
        job.organization_id = uuid4()
    elif change == "other_candidate":
        job.payload["options"]["candidate_id"] = "sha256:" + "0" * 64
    elif change == "missing_org":
        job.payload["options"].pop("accountability_organization_id")
    else:
        projection.result["solution_id"] = str(uuid4())
    with pytest.raises(AccountabilityRecoveryConflict):
        await validate_recovery_deployment(
            db, solution_id=solution.id, deploy_job_id=job_id
        )
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_changed_live_artifact_cannot_be_reconciled():
    db, solution, _, _, job_id = deployment()
    lock = AsyncMock()
    with (
        patch(
            "src.services.solutions.accountability_recovery.solution_write_lock",
            return_value=lock,
        ),
        patch(
            "src.services.solutions.accountability_recovery.SolutionSourceArtifactStorage"
        ) as storage,
        patch(
            "src.services.solutions.accountability_recovery.reconcile_solution_deploy_obligation",
            new_callable=AsyncMock,
        ) as reconcile,
    ):
        storage.return_value.read = AsyncMock(return_value=b"different deployment")
        with pytest.raises(AccountabilityRecoveryConflict, match="differs"):
            await recover_solution_deploy_accountability(
                db, solution_id=solution.id, deploy_job_id=job_id
            )
        reconcile.assert_not_called()
        db.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["released", "attention_required"])
async def test_persists_reconciliation_outcome_without_redeploying(state):
    db, solution, _, _, job_id = deployment()
    lock = AsyncMock()
    outcome = {"state": state, "obligation_id": str(uuid4())}
    with (
        patch(
            "src.services.solutions.accountability_recovery.solution_write_lock",
            return_value=lock,
        ),
        patch(
            "src.services.solutions.accountability_recovery.SolutionSourceArtifactStorage"
        ) as storage,
        patch(
            "src.services.solutions.accountability_recovery.reconcile_solution_deploy_obligation",
            new_callable=AsyncMock,
            return_value=outcome,
        ) as reconcile,
    ):
        storage.return_value.read = AsyncMock(return_value=b"artifact")
        result = await recover_solution_deploy_accountability(
            db, solution_id=solution.id, deploy_job_id=job_id
        )
        assert result["source_release_accountability"] == outcome
        assert reconcile.call_args.kwargs["artifact"] == b"artifact"
        db.commit.assert_awaited_once()
        storage.return_value.write.assert_not_called()


@pytest.mark.asyncio
async def test_repeated_recovery_preserves_existing_proof_without_reconciling_newer_pending_source():
    db, solution, _, _, job_id = deployment()
    evidence_id = "sha256:proof"
    released = SimpleNamespace(
        id=uuid4(), completion_evidence={"evidence_id": evidence_id}
    )
    db.scalar.side_effect = [None, released]
    with (
        patch(
            "src.services.solutions.accountability_recovery.solution_write_lock",
            return_value=AsyncMock(),
        ),
        patch(
            "src.services.solutions.accountability_recovery.SolutionSourceArtifactStorage"
        ) as storage,
        patch(
            "src.services.solutions.accountability_recovery.reconcile_solution_deploy_obligation",
            new_callable=AsyncMock,
            return_value={"state": "attention_required"},
        ) as reconcile,
        patch(
            "src.services.solutions.accountability_recovery.verify_solution_artifact",
            return_value=(True, None, {}),
        ),
        patch(
            "src.services.solutions.accountability_recovery._runtime_and_registration_readback",
            new_callable=AsyncMock,
            return_value=(True, None, {}),
        ) as readback,
    ):
        storage.return_value.read = AsyncMock(return_value=b"artifact")
        result = await recover_solution_deploy_accountability(
            db, solution_id=solution.id, deploy_job_id=job_id
        )
        assert result["source_release_accountability"] == {
            "state": "released",
            "obligation_id": str(released.id),
            "evidence_id": evidence_id,
        }
        readback.assert_awaited_once()
        reconcile.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("global_install", [True, False])
async def test_uses_original_tracking_org_independent_of_install_scope(global_install):
    db, solution, job, _, job_id = deployment()
    tracking_org = uuid4()
    job.payload["options"]["accountability_organization_id"] = str(tracking_org)
    if global_install:
        solution.organization_id = None
        job.organization_id = None
    _, _, observed_org = await validate_recovery_deployment(
        db, solution_id=solution.id, deploy_job_id=job_id
    )
    assert observed_org == tracking_org


@pytest.mark.asyncio
async def test_zip_install_can_use_result_identity_with_nullable_projection():
    db, solution, job, projection, job_id = deployment()
    job.payload["kind"] = "install"
    job.payload["install_id"] = None
    projection.install_id = None
    actual, _, _ = await validate_recovery_deployment(
        db, solution_id=solution.id, deploy_job_id=job_id
    )
    assert actual is solution


@pytest.mark.asyncio
async def test_newer_failed_deploy_attempt_blocks_recovery_of_older_artifact():
    db, solution, _, _, job_id = deployment()
    db.scalar.return_value = uuid4()
    with pytest.raises(
        AccountabilityRecoveryConflict, match="newer deployment attempt"
    ):
        await validate_recovery_deployment(
            db, solution_id=solution.id, deploy_job_id=job_id
        )
    query = str(db.scalar.call_args.args[0])
    assert "created_at >" in query
    assert "status" not in query
    db.commit.assert_not_called()
