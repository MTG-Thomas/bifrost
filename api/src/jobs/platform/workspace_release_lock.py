"""Durable compatibility/history lock-in for one immutable Workspace release."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.models.orm.workspace_promotions import (
    WorkspacePromotionArtifact,
    WorkspacePromotionRelease,
)
from src.models.orm.platform_jobs import PlatformJob
from src.services.github_config import get_github_config
from src.services.platform_commit_writer import GitHubAppCommitWriter
from src.services.platform_jobs import enqueue_platform_job
from src.services.workspace_release_projection import (
    WorkspaceReleaseProjectionError,
    WorkspaceReleaseProjectionService,
    acquire_workspace_release_lock,
)
from src.services.workspace_release_runtime import WorkspaceReleaseDescriptor

WORKSPACE_RELEASE_LOCK_JOB_TYPE = "workspace.release.lock"


class WorkspaceReleaseLockPayload(BaseModel):
    release_row_id: UUID
    release_id: str


def _commit_writer(config):
    settings = get_settings()
    if (
        not config
        or not config.repo_url
        or config.branch.removeprefix("refs/heads/") != "production-live"
    ):
        return None
    if not settings.github_app_commit_writer_configured:
        return None
    if (
        settings.github_app_id is None
        or settings.github_app_installation_id is None
        or settings.github_app_private_key is None
    ):
        return None
    return GitHubAppCommitWriter(
        repo_url=config.repo_url,
        branch=config.branch,
        app_id=settings.github_app_id,
        installation_id=settings.github_app_installation_id,
        private_key=settings.github_app_private_key.get_secret_value(),
    )


async def run_workspace_release_lock(
    context: PlatformJobContext,
    raw_payload: BaseModel,
) -> dict[str, object]:
    payload = WorkspaceReleaseLockPayload.model_validate(raw_payload)
    if context.organization_id is None:
        raise PlatformJobFailure(
            "workspace_release_org_missing",
            "Workspace release lock-in requires an organization.",
        )
    await context.report("Classifying compatibility and signed history", 0, 3, 5)
    try:
        async with get_db_context() as db:
            config = await get_github_config(db, context.organization_id)
            evidence = await WorkspaceReleaseProjectionService(
                db,
                context.organization_id,
                commit_writer=_commit_writer(config),
            ).lock_release(
                payload.release_row_id,
                payload.release_id,
                operator=context.requested_by_email,
                report=context.report,
            )
        await context.report("Workspace release lock-in complete", 3, 3, 100)
        await context.log(
            "info",
            "workspace_release_locked",
            f"Workspace release {payload.release_id} projection state: "
            f"{evidence['state']}",
        )
        return {
            "release_row_id": str(payload.release_row_id),
            "release_id": payload.release_id,
            "lock_state": str(evidence["state"]),
            "lock_evidence_id": str(evidence["evidence_id"]),
            "history_commit_sha": (
                (evidence.get("history_after") or {}).get("commit_sha")
            ),
            "release_ledger_path": ((evidence.get("release_ledger") or {}).get("path")),
            "release_ledger_sha256": (
                (evidence.get("release_ledger") or {}).get("sha256")
            ),
        }
    except WorkspaceReleaseProjectionError as exc:
        raise PlatformJobFailure(
            exc.code,
            str(exc),
            retryable=exc.retryable,
        ) from exc


async def enqueue_workspace_release_lock(
    db: AsyncSession,
    *,
    release: WorkspacePromotionRelease,
    artifact: WorkspacePromotionArtifact,
    requested_by_user_id: UUID,
    requested_by_email: str,
    requested_by_name: str,
) -> tuple[PlatformJob, bool]:
    """Queue one globally serialized idempotent projection after activation."""
    await acquire_workspace_release_lock(db, release.organization_id)
    if release.activation_state != "live":
        raise ValueError("only the current Live Workspace release can be locked")
    if release.lock_state in {"queued", "in_progress", "locked"}:
        if release.lock_in_job_id is None:
            raise ValueError(
                "Workspace release lock state is missing its durable platform job"
            )
        existing = await db.scalar(
            select(PlatformJob).where(PlatformJob.id == release.lock_in_job_id)
        )
        if existing is None:
            raise ValueError(
                "Workspace release lock state references a missing platform job"
            )
        return existing, True
    if release.lock_state not in {"not_queued", "attention_required"}:
        raise ValueError(
            f"Workspace release lock state {release.lock_state!r} cannot be queued"
        )
    descriptor = WorkspaceReleaseDescriptor.from_rows(release, artifact)
    job, reused = await enqueue_platform_job(
        db,
        WORKSPACE_RELEASE_LOCK_DEFINITION,
        WorkspaceReleaseLockPayload(
            release_row_id=release.id,
            release_id=descriptor.release_id,
        ),
        dedupe_key=f"{release.id}:{descriptor.release_id}",
        resource_lock_key="workspace-release",
        priority=200,
        organization_id=release.organization_id,
        requested_by_user_id=requested_by_user_id,
        requested_by_email=requested_by_email,
        requested_by_name=requested_by_name,
        resource_type="workspace_release",
        resource_id=str(release.id),
        title="Lock immutable Workspace release",
        action_url=None,
    )
    release.lock_state = "queued"
    release.lock_in_job_id = job.id
    await db.flush()
    return job, reused


WORKSPACE_RELEASE_LOCK_DEFINITION = PlatformJobDefinition(
    job_type=WORKSPACE_RELEASE_LOCK_JOB_TYPE,
    payload_version=1,
    payload_model=WorkspaceReleaseLockPayload,
    handler=run_workspace_release_lock,
    policy=PlatformJobPolicy(
        timeout_seconds=15 * 60,
        max_attempts=2,
        max_concurrency=1,
        retry_on_runner_loss=True,
        min_memory_headroom_mb=256,
    ),
)


__all__ = [
    "WORKSPACE_RELEASE_LOCK_DEFINITION",
    "WORKSPACE_RELEASE_LOCK_JOB_TYPE",
    "WorkspaceReleaseLockPayload",
    "enqueue_workspace_release_lock",
]
