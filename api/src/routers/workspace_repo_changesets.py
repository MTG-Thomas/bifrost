"""Thin HTTP surface for transactional workspace _repo changesets."""

import logging

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from src.core.auth import Context, CurrentSuperuser
from src.core.db_deps import DbSession
from src.models.contracts.workspace_repo_changesets import (
    WorkspaceRepoActivateRequest,
    WorkspaceRepoChangesetBegin,
    WorkspaceRepoChangesetDiffResponse,
    WorkspaceRepoChangesetResponse,
    WorkspaceRepoChangesetListResponse,
    WorkspaceRepoFileMutationRequest,
    WorkspaceRepoStateResponse,
    WorkspaceRepoOperationalStatusResponse,
    WorkspaceDirtyStatus,
    WorkspaceWriterStatus,
    WorkspaceAuthoritativeConvergenceResponse,
    WorkspaceRepoValidationResponse,
)
from src.models.contracts.platform_jobs import PlatformJobAccepted
from src.jobs.platform.workspace_repo_closure import (
    WORKSPACE_REPO_CLOSURE_DEFINITION,
    WorkspaceRepoClosurePayload,
)
from src.services.workspace_repo_changesets import (
    ChangesetConflict,
    ChangesetInvalid,
    OrganizationScopeRequired,
    WorkspaceRepoChangesetService,
    build_workspace_repo_changeset_service,
    require_organization_id,
)

router = APIRouter(
    prefix="/api/workspace-repo-changesets",
    tags=["Workspace _repo compatibility changesets"],
)
logger = logging.getLogger(__name__)


async def _service(db: DbSession, org_id: UUID | None) -> WorkspaceRepoChangesetService:
    org_id = require_organization_id(org_id)
    return await build_workspace_repo_changeset_service(db, org_id)


async def _enqueue_writer(
    *,
    changeset_id: UUID,
    request: WorkspaceRepoActivateRequest,
    operation: Literal["activate", "retry"],
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
    response: Response,
) -> PlatformJobAccepted:
    from src.core.workspace_writer import (
        WORKSPACE_WRITER_RESOURCE_LOCK,
        lock_workspace_writer_gate,
    )
    from src.services.platform_jobs import (
        ensure_platform_job_notification,
        enqueue_platform_job,
        publish_platform_job_update,
    )

    organization_id = require_organization_id(ctx.org_id)
    service = await _service(db, organization_id)
    changeset = await service.get(changeset_id)
    if operation == "activate" and changeset.status not in {"validated", "activating"}:
        raise ChangesetInvalid(
            "changeset must pass validation immediately before activation"
        )
    if operation == "retry":
        await service.check_retryable_git_closure(changeset_id, request)
    await lock_workspace_writer_gate(db)
    job, reused = await enqueue_platform_job(
        db,
        WORKSPACE_REPO_CLOSURE_DEFINITION,
        WorkspaceRepoClosurePayload(
            changeset_id=changeset_id,
            organization_id=organization_id,
            operation=operation,
            commit_message=request.commit_message,
            push=request.push,
            updated_by=user.email,
            plan_id=request.plan_id,
            protected_main_source_sha=request.protected_main_source_sha,
        ),
        dedupe_key=f"{operation}:{changeset_id}",
        resource_lock_key=WORKSPACE_WRITER_RESOURCE_LOCK,
        priority=1000,
        organization_id=organization_id,
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.name or user.email,
        resource_type="workspace_repo_changeset",
        resource_id=str(changeset_id),
        title=(
            f"Activating workspace changeset {changeset_id}"
            if operation == "activate"
            else f"Retrying workspace closure {changeset_id}"
        ),
        action_url="/diagnostics",
    )
    if reused and job.requested_by_user_id != str(user.user_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This workspace changeset already has an active writer",
        )
    await service.assign_writer_job(changeset_id, job.id)
    if job.notification_id is None:
        try:
            await ensure_platform_job_notification(db, job)
        except Exception:
            logger.warning(
                "Workspace writer queued without a progress notification",
                extra={"platform_job_id": str(job.id)},
                exc_info=True,
            )
    await db.commit()
    await db.refresh(job)
    await publish_platform_job_update(job)
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    return PlatformJobAccepted(
        job_id=job.id,
        status=job.status,
        reused=reused,
        notification_id=job.notification_id,
    )


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, ChangesetConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.detail)
    if isinstance(exc, ChangesetInvalid):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    if isinstance(exc, OrganizationScopeRequired):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="workspace _repo changeset not found",
        )
    raise exc


@router.get("/state", response_model=WorkspaceRepoStateResponse)
async def workspace_repo_state(
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
    scope: str = Query(..., min_length=1),
):
    try:
        from src.core.repo_dirty import get_repo_dirty_since
        from src.services.github_config import get_github_config

        config = await get_github_config(db, ctx.org_id)
        dirty_since = await get_repo_dirty_since()
        git_status = {
            "configured": bool(config and config.repo_url),
            "branch": config.branch if config and config.repo_url else None,
            "dirty_since": dirty_since,
        }
        return await (await _service(db, ctx.org_id)).state(
            scope, git_status=git_status, workspace_dirty=dirty_since is not None
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "",
    response_model=WorkspaceRepoChangesetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def begin_workspace_repo_changeset(
    request: WorkspaceRepoChangesetBegin,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
):
    try:
        return await (await _service(db, ctx.org_id)).begin(request, user.user_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("", response_model=WorkspaceRepoChangesetListResponse)
async def list_workspace_repo_changesets(
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
):
    try:
        service = await _service(db, ctx.org_id)
        return WorkspaceRepoChangesetListResponse(
            changesets=await service.list_active()
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/operational-status",
    response_model=WorkspaceRepoOperationalStatusResponse,
)
async def workspace_repo_operational_status(
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
):
    from sqlalchemy import select

    from src.core.repo_dirty import get_repo_dirty_state
    from src.core.workspace_writer import WORKSPACE_WRITER_RESOURCE_LOCK
    from src.models.orm.platform_jobs import PlatformJob
    from src.services.github_config import (
        build_authenticated_github_url,
        get_github_config,
    )
    from src.services.github_sync import GitHubSyncService

    try:
        service = await _service(db, ctx.org_id)
        active = await service.list_active()
        recoverable = await service.recoverable_git_closures()
        ledger = await service.closure_ledger()
        dirty = await get_repo_dirty_state()
        writer = (
            await db.execute(
                select(PlatformJob)
                .where(
                    PlatformJob.resource_lock_key
                    == WORKSPACE_WRITER_RESOURCE_LOCK,
                    PlatformJob.status.in_(
                        ("queued", "running", "waiting", "cancel_requested")
                    ),
                )
                .order_by(PlatformJob.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        config = await get_github_config(db, ctx.org_id)
        convergence = WorkspaceAuthoritativeConvergenceResponse(
            configured=False,
            branch=config.branch if config and config.repo_url else None,
        )
        changeset_id = None
        if writer and writer.resource_id:
            try:
                changeset_id = UUID(writer.resource_id)
            except ValueError:
                pass
        writer_status = (
            WorkspaceWriterStatus(
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
            if writer
            else None
        )
        if config and config.repo_url and config.token:
            # The preceding rows are projected already; close the read
            # transaction before the remote fetch/readback network boundary.
            await db.commit()
            convergence = await GitHubSyncService(
                db,
                build_authenticated_github_url(config.repo_url, config.token),
                config.branch,
            ).authoritative_convergence()
        return WorkspaceRepoOperationalStatusResponse(
            dirty=WorkspaceDirtyStatus(
                dirty=dirty is not None,
                generation=dirty.generation if dirty else None,
                dirty_since=dirty.dirty_since if dirty else None,
                updated_at=dirty.updated_at if dirty else None,
                writer=dirty.writer if dirty else None,
                legacy=dirty.legacy if dirty else False,
            ),
            active_writer=writer_status,
            active_changesets=active,
            recoverable_closures=recoverable,
            closure_ledger=ledger,
            convergence=convergence,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/recoverable-git-closures",
    response_model=list[WorkspaceRepoChangesetResponse],
)
async def list_recoverable_workspace_repo_git_closures(
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
    scope: str | None = None,
):
    try:
        return await (await _service(db, ctx.org_id)).recoverable_git_closures(
            scope=scope
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/{changeset_id}", response_model=WorkspaceRepoChangesetResponse)
async def show_workspace_repo_changeset(
    changeset_id: UUID, ctx: Context, db: DbSession, user: CurrentSuperuser
):
    try:
        return await (await _service(db, ctx.org_id)).get(changeset_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/{changeset_id}/files", response_model=WorkspaceRepoChangesetResponse)
async def stage_workspace_repo_file(
    changeset_id: UUID,
    request: WorkspaceRepoFileMutationRequest,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
):
    try:
        return await (await _service(db, ctx.org_id)).stage(changeset_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/{changeset_id}/diff", response_model=WorkspaceRepoChangesetDiffResponse)
async def workspace_repo_changeset_diff(
    changeset_id: UUID, ctx: Context, db: DbSession, user: CurrentSuperuser
):
    try:
        return await (await _service(db, ctx.org_id)).diff(changeset_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/{changeset_id}/validate", response_model=WorkspaceRepoValidationResponse)
async def validate_workspace_repo_changeset(
    changeset_id: UUID, ctx: Context, db: DbSession, user: CurrentSuperuser
):
    try:
        return await (await _service(db, ctx.org_id)).validate(changeset_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/{changeset_id}/activate",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def activate_workspace_repo_changeset(
    changeset_id: UUID,
    request: WorkspaceRepoActivateRequest,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
    response: Response,
):
    try:
        return await _enqueue_writer(
            changeset_id=changeset_id,
            request=request,
            operation="activate",
            ctx=ctx,
            db=db,
            user=user,
            response=response,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/{changeset_id}/retry-git-closure",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_workspace_repo_git_closure(
    changeset_id: UUID,
    request: WorkspaceRepoActivateRequest,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
    response: Response,
):
    try:
        return await _enqueue_writer(
            changeset_id=changeset_id,
            request=request,
            operation="retry",
            ctx=ctx,
            db=db,
            user=user,
            response=response,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/{changeset_id}/abort", response_model=WorkspaceRepoChangesetResponse)
async def abort_workspace_repo_changeset(
    changeset_id: UUID, ctx: Context, db: DbSession, user: CurrentSuperuser
):
    try:
        return await (await _service(db, ctx.org_id)).abort(changeset_id)
    except Exception as exc:
        raise _translate(exc) from exc
