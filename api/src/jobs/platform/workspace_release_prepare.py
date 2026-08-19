"""Durable immutable Workspace release preparation job."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.services.workspace_release_materialization import (
    WorkspaceReleaseMaterializer,
    WorkspaceReleasePreparationError,
)

WORKSPACE_RELEASE_PREPARE_JOB_TYPE = "workspace.release.prepare"


class WorkspaceReleasePreparePayload(BaseModel):
    artifact_id: UUID
    candidate_id: str


async def run_workspace_release_prepare(
    context: PlatformJobContext,
    raw_payload: BaseModel,
) -> dict[str, object]:
    payload = WorkspaceReleasePreparePayload.model_validate(raw_payload)
    if context.organization_id is None:
        raise PlatformJobFailure(
            "workspace_release_org_missing",
            "Workspace release preparation requires an organization.",
        )
    try:
        async with get_db_context() as db:
            release, evidence = await WorkspaceReleaseMaterializer(
                db, context.organization_id
            ).prepare(
                payload.artifact_id,
                payload.candidate_id,
                UUID(context.requested_by_user_id),
                report=context.report,
            )
        await context.log(
            "info",
            "workspace_release_prepared",
            f"Workspace release {evidence['release_id']} prepared from immutable source",
        )
        return {
            "release_row_id": str(release.id),
            "artifact_id": str(payload.artifact_id),
            "candidate_id": payload.candidate_id,
            "release_id": str(evidence["release_id"]),
            "prepared_evidence_id": str(evidence["evidence_id"]),
            "runtime_storage_prefix": str(evidence["runtime_storage_prefix"]),
            "file_count": int(evidence["file_count"]),
            "total_bytes": int(evidence["total_bytes"]),
        }
    except WorkspaceReleasePreparationError as exc:
        raise PlatformJobFailure(
            "workspace_release_prepare_failed",
            str(exc),
            retryable=False,
        ) from exc
    except ValueError as exc:
        raise PlatformJobFailure(
            "workspace_release_prepare_invalid",
            str(exc),
            retryable=False,
        ) from exc


WORKSPACE_RELEASE_PREPARE_DEFINITION = PlatformJobDefinition(
    job_type=WORKSPACE_RELEASE_PREPARE_JOB_TYPE,
    payload_version=1,
    payload_model=WorkspaceReleasePreparePayload,
    handler=run_workspace_release_prepare,
    policy=PlatformJobPolicy(
        timeout_seconds=15 * 60,
        max_attempts=2,
        max_concurrency=2,
        retry_on_runner_loss=True,
        min_memory_headroom_mb=512,
    ),
)
