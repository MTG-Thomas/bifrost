"""Shared operational projection for the authoritative workspace repository."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.repo_dirty import RepoDirtyState, get_repo_dirty_state
from src.core.workspace_writer import WORKSPACE_WRITER_RESOURCE_LOCK
from src.models.contracts.workspace_repo_changesets import (
    WorkspaceAuthoritativeConvergenceResponse,
    WorkspaceWriterStatus,
)
from src.models.orm.platform_jobs import PlatformJob
from src.repositories.workspace_repo_changesets import WorkspaceRepoChangesetRepository
from src.services.github_config import build_authenticated_github_url, get_github_config
from src.services.github_sync import GitHubSyncService


@dataclass(frozen=True)
class WorkspaceOperationalSnapshot:
    dirty: RepoDirtyState | None
    writer: WorkspaceWriterStatus | None
    convergence: WorkspaceAuthoritativeConvergenceResponse
    active_changeset_count: int
    recoverable_closure_count: int


async def get_active_workspace_writer_status(
    db: AsyncSession,
) -> WorkspaceWriterStatus | None:
    """Return the global lease holder before any queued writer contender."""
    writer = (
        await db.execute(
            select(PlatformJob)
            .where(
                PlatformJob.resource_lock_key == WORKSPACE_WRITER_RESOURCE_LOCK,
                PlatformJob.status.in_(
                    ("queued", "running", "waiting", "cancel_requested")
                ),
            )
            .order_by(
                case((PlatformJob.lease_token.is_not(None), 0), else_=1),
                PlatformJob.created_at.asc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if writer is None:
        return None

    changeset_id = None
    if writer.resource_type == "workspace_repo_changeset" and writer.resource_id:
        changeset_id = UUID(writer.resource_id)
    return WorkspaceWriterStatus(
        job_id=writer.id,
        changeset_id=changeset_id,
        status=writer.status,
        phase=writer.phase,
        lease_owner=writer.lease_owner,
        lease_expires_at=writer.lease_expires_at,
        lease_expired=(
            writer.lease_expires_at is not None
            and writer.lease_expires_at <= datetime.now(timezone.utc)
        ),
        started_at=writer.started_at,
    )


async def get_authoritative_convergence_status(
    db: AsyncSession, organization_id: UUID | None
) -> WorkspaceAuthoritativeConvergenceResponse:
    """Read configured remote convergence without holding a DB transaction."""
    config = await get_github_config(db, organization_id)
    result = WorkspaceAuthoritativeConvergenceResponse(
        configured=False,
        branch=config.branch if config and config.repo_url else None,
    )
    if not (config and config.repo_url and config.token):
        return result
    await db.commit()
    return await GitHubSyncService(
        db,
        build_authenticated_github_url(config.repo_url, config.token),
        config.branch,
    ).authoritative_convergence()


async def get_workspace_operational_snapshot(
    db: AsyncSession, organization_id: UUID | None
) -> WorkspaceOperationalSnapshot:
    """Build the common exact-path status consumed by both supported routes."""
    dirty = await get_repo_dirty_state()
    writer = await get_active_workspace_writer_status(db)
    convergence = await get_authoritative_convergence_status(db, organization_id)
    active_count = 0
    recoverable_count = 0
    if organization_id is not None:
        rows = WorkspaceRepoChangesetRepository(db)
        active_count = await rows.count_by_statuses(
            organization_id, ("open", "staged", "validated", "activating")
        )
        recoverable_count = await rows.count_retryable_git_failures(organization_id)
    return WorkspaceOperationalSnapshot(
        dirty=dirty,
        writer=writer,
        convergence=convergence,
        active_changeset_count=active_count,
        recoverable_closure_count=recoverable_count,
    )
