"""Reconcile deployment evidence without changing installed Solution resources."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import decrypt_secret
from src.jobs.platform.solution_deploy import SolutionDeployPayload
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution
from src.models.orm.workspace_promotions import SolutionDeployObligation
from src.services.solution_deploy_obligations import (
    reconcile_solution_deploy_obligation,
    _runtime_and_registration_readback,
    verify_solution_artifact,
)
from src.services.solutions.source_artifact import SolutionSourceArtifactStorage
from src.services.solutions.write_lock import solution_write_lock


class AccountabilityRecoveryConflict(ValueError):
    """The supplied deployment no longer proves the installed candidate."""


async def validate_recovery_deployment(
    db: AsyncSession,
    *,
    solution_id: UUID,
    deploy_job_id: UUID,
) -> tuple[Solution, SolutionDeployPayload, UUID]:
    solution = await db.get(Solution, solution_id)
    job = await db.get(PlatformJob, deploy_job_id)
    projection = await db.get(SolutionDeployJob, deploy_job_id)
    if solution is None or job is None or projection is None:
        raise KeyError("Solution or deployment not found")
    if (
        job.job_type != "solution.deploy"
        or job.status != "succeeded"
        or projection.status != "succeeded"
    ):
        raise AccountabilityRecoveryConflict(
            "Recovery requires a successful Solution deployment"
        )
    try:
        payload = (
            SolutionDeployPayload.model_validate_json(
                decrypt_secret(job.encrypted_payload)
            )
            if job.encrypted_payload
            else SolutionDeployPayload.model_validate(job.payload)
        )
        organization_id = UUID(str(payload.options["accountability_organization_id"]))
    except (ValueError, KeyError, TypeError) as exc:
        raise AccountabilityRecoveryConflict(
            "Deployment accountability evidence is unavailable"
        ) from exc
    expected_candidate = f"sha256:{payload.input_sha256}"
    if (
        payload.deploy_job_id != deploy_job_id
        or (payload.install_id is not None and payload.install_id != solution_id)
        or (
            projection.install_id != solution_id
            and not (payload.kind == "install" and projection.install_id is None)
        )
        or job.organization_id != solution.organization_id
        or payload.options.get("candidate_id") != expected_candidate
        or len(payload.input_sha256) != 64
        or any(
            character not in "0123456789abcdef" for character in payload.input_sha256
        )
        or any(
            result.get("solution_id") != str(solution_id)
            or result.get("candidate_id") != expected_candidate
            for result in (job.result or {}, projection.result or {})
        )
    ):
        raise AccountabilityRecoveryConflict(
            "Deployment identity, candidate, or organization evidence disagrees"
        )
    newer_attempt = await db.scalar(
        select(SolutionDeployJob.id)
        .where(
            SolutionDeployJob.created_at > projection.created_at,
            or_(
                SolutionDeployJob.install_id == solution_id,
                SolutionDeployJob.result["solution_id"].as_string() == str(solution_id),
            ),
        )
        .limit(1)
    )
    if newer_attempt is not None:
        # A failed deploy may already have committed metadata while leaving the
        # old source ZIP in storage. That ZIP alone cannot prove the old install.
        raise AccountabilityRecoveryConflict(
            "A newer deployment attempt exists for this Solution"
        )
    return solution, payload, organization_id


async def recover_solution_deploy_accountability(
    db: AsyncSession,
    *,
    solution_id: UUID,
    deploy_job_id: UUID,
) -> dict[str, Any]:
    async with solution_write_lock(solution_id):
        solution, payload, organization_id = await validate_recovery_deployment(
            db,
            solution_id=solution_id,
            deploy_job_id=deploy_job_id,
        )
        artifact = await SolutionSourceArtifactStorage(solution_id).read()
        if (
            artifact is None
            or hashlib.sha256(artifact).hexdigest() != payload.input_sha256
        ):
            raise AccountabilityRecoveryConflict(
                "Installed source artifact differs from the successful deployment"
            )
        # A retry after the evidence commit must return the original proof,
        # while still checking that the installed runtime has not drifted.
        released = await db.scalar(
            select(SolutionDeployObligation)
            .where(
                SolutionDeployObligation.organization_id == organization_id,
                SolutionDeployObligation.solution_id == solution_id,
                SolutionDeployObligation.deploy_job_id == deploy_job_id,
                SolutionDeployObligation.candidate_id
                == payload.options["candidate_id"],
                SolutionDeployObligation.disposition == "released",
            )
            .order_by(SolutionDeployObligation.created_at.desc())
            .limit(1)
        )
        if released is not None:
            valid, reason, _ = verify_solution_artifact(
                released,
                candidate_id=str(payload.options["candidate_id"]),
                artifact=artifact,
            )
            if not valid:
                raise AccountabilityRecoveryConflict(
                    reason or "Released source evidence disagrees"
                )
            valid, reason, _ = await _runtime_and_registration_readback(
                db,
                solution_id=solution_id,
                artifact=artifact,
            )
            if not valid:
                raise AccountabilityRecoveryConflict(
                    reason or "Installed runtime differs from released evidence"
                )
            accountability = {
                "state": "released",
                "obligation_id": str(released.id),
                "evidence_id": (released.completion_evidence or {}).get("evidence_id"),
            }
        else:
            accountability = await reconcile_solution_deploy_obligation(
                db,
                solution_id=solution_id,
                solution_slug=solution.slug,
                accountability_organization_id=organization_id,
                deploy_job_id=deploy_job_id,
                candidate_id=str(payload.options["candidate_id"]),
                artifact=artifact,
            )
        # Commit evidence before releasing the same lock used by deploy/finalize.
        await db.commit()
        return {
            "solution_id": str(solution_id),
            "deploy_job_id": str(deploy_job_id),
            "candidate_id": payload.options["candidate_id"],
            "source_release_accountability": accountability,
        }
