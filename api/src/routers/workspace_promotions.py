"""Immutable rapid Workspace artifact, preparation, and canary HTTP surface."""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from src.config import get_settings
from src.core.auth import Context, CurrentSuperuser
from src.core.db_deps import DbSession
from src.models.contracts.workspace_promotions import (
    WorkspacePromotionCanaryAccepted,
    WorkspacePromotionCanaryRequest,
    WorkspacePromotionPreviewRequest,
    WorkspacePromotionPreviewResponse,
    WorkspacePromotionArtifactResponse,
    WorkspacePromotionDraftRequest,
    WorkspacePromotionDraftResponse,
    WorkspaceLiveStatusResponse,
    WorkspaceReleaseActivateRequest,
    WorkspaceReleasePrepareRequest,
    WorkspaceReleaseStatusResponse,
)
from src.models.contracts.platform_jobs import PlatformJobAccepted
from src.jobs.platform.workspace_release_prepare import (
    WORKSPACE_RELEASE_PREPARE_DEFINITION,
    WorkspaceReleasePreparePayload,
)
from src.services.platform_jobs import (
    enqueue_platform_job,
    ensure_platform_job_notification,
    publish_platform_job_update,
)
from src.services.workspace_draft_canary import (
    WorkspaceDraftCanaryError,
    WorkspaceDraftCanaryService,
)
from src.services.workspace_release_activation import (
    WorkspaceReleaseActivationError,
    WorkspaceReleaseActivationService,
)
from src.services.workspace_promotions import (
    WorkspacePromotionInvalid,
    WorkspacePromotionPreviewService,
)

router = APIRouter(
    prefix="/api/workspace-promotions",
    tags=["Workspace rapid promotion"],
)
logger = logging.getLogger(__name__)


async def _service(
    db: DbSession, organization_id: UUID
) -> WorkspacePromotionPreviewService:
    from src.services.github_config import get_github_config
    from src.services.platform_commit_writer import GitHubAppCommitWriter

    settings = get_settings()
    config = await get_github_config(db, organization_id)
    writer = None
    if config and config.repo_url and settings.github_app_commit_writer_configured:
        if (
            settings.github_app_id is None
            or settings.github_app_installation_id is None
            or settings.github_app_private_key is None
        ):
            raise RuntimeError("GitHub App commit writer configuration is incomplete")
        writer = GitHubAppCommitWriter(
            repo_url=config.repo_url,
            branch=config.branch,
            app_id=settings.github_app_id,
            installation_id=settings.github_app_installation_id,
            private_key=settings.github_app_private_key.get_secret_value(),
        )
    return WorkspacePromotionPreviewService(
        db, organization_id, commit_writer=writer
    )


@router.post("/preview", response_model=WorkspacePromotionPreviewResponse)
async def preview_workspace_promotion(
    request: WorkspacePromotionPreviewRequest,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
):
    settings = get_settings()
    if not settings.workspace_rapid_promotion_preview_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="rapid Workspace promotion preview is not enabled",
        )
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    try:
        return await (await _service(db, ctx.org_id)).preview(request, user.user_id)
    except WorkspacePromotionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.post(
    "/drafts",
    response_model=WorkspacePromotionDraftResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_workspace_promotion_draft(
    request: WorkspacePromotionDraftRequest,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
) -> WorkspacePromotionDraftResponse:
    settings = get_settings()
    if not settings.workspace_rapid_promotion_draft_upload_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="local Workspace draft upload is not enabled",
        )
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    try:
        return await (await _service(db, ctx.org_id)).upload_draft(
            request, user.user_id
        )
    except WorkspacePromotionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get(
    "/artifacts/{artifact_id}", response_model=WorkspacePromotionArtifactResponse
)
async def get_workspace_promotion_artifact(
    artifact_id: UUID,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
):
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    try:
        return await (await _service(db, ctx.org_id)).get_artifact(artifact_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace release artifact not found",
        ) from exc


@router.post(
    "/artifacts/{artifact_id}/canary",
    response_model=WorkspacePromotionCanaryAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def execute_workspace_promotion_canary(
    artifact_id: UUID,
    request: WorkspacePromotionCanaryRequest,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
):
    settings = get_settings()
    if not settings.workspace_release_prepare_canary_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace release canaries are not enabled",
        )
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    service = WorkspaceDraftCanaryService(db, ctx.org_id)
    try:
        execution_id = await service.issue(
            artifact_id,
            request.parameters,
            user_id=user.user_id,
            user_name=user.name,
            user_email=user.email,
            is_platform_admin=user.is_platform_admin,
            is_provider_org=user.is_provider_org,
            is_external=user.is_external,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace release artifact not found",
        ) from exc
    except WorkspaceDraftCanaryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return WorkspacePromotionCanaryAccepted(
        execution_id=execution_id,
        artifact_id=artifact_id,
    )


@router.post(
    "/artifacts/{artifact_id}/prepare",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def prepare_workspace_release(
    artifact_id: UUID,
    request: WorkspaceReleasePrepareRequest,
    response: Response,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
) -> PlatformJobAccepted:
    settings = get_settings()
    if not settings.workspace_release_prepare_canary_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace release preparation is not enabled",
        )
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    job, reused = await enqueue_platform_job(
        db,
        WORKSPACE_RELEASE_PREPARE_DEFINITION,
        WorkspaceReleasePreparePayload(
            artifact_id=artifact_id,
            candidate_id=request.candidate_id,
        ),
        dedupe_key=f"{artifact_id}:{request.candidate_id}",
        resource_lock_key=f"workspace-release:{ctx.org_id}",
        organization_id=ctx.org_id,
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.name or user.email or "Unknown",
        resource_type="workspace_promotion_artifact",
        resource_id=str(artifact_id),
        title="Preparing immutable Workspace release",
        action_url=None,
    )
    if reused and job.requested_by_user_id != str(user.user_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workspace release preparation is already in progress",
        )
    if job.notification_id is None:
        try:
            await ensure_platform_job_notification(db, job)
        except Exception:
            logger.warning(
                "Workspace release preparation queued without notification",
                extra={"platform_job_id": str(job.id)},
                exc_info=True,
            )
    await db.commit()
    await db.refresh(job)
    await publish_platform_job_update(job)
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    return PlatformJobAccepted(
        job_id=job.id,
        notification_id=job.notification_id,
        status=job.status,
        reused=reused,
    )


@router.post(
    "/releases/{release_id}/activate",
    response_model=WorkspaceReleaseStatusResponse,
)
async def activate_workspace_release(
    release_id: UUID,
    request: WorkspaceReleaseActivateRequest,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
) -> WorkspaceReleaseStatusResponse:
    if not get_settings().workspace_rapid_promotion_preview_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="rapid Workspace promotion is not enabled",
        )
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    try:
        service = WorkspaceReleaseActivationService(db, ctx.org_id)
        await service.activate(release_id, request)
        result, job, _reused = await service.enqueue_projection(
            release_id,
            requested_by_user_id=user.user_id,
            requested_by_email=user.email,
            requested_by_name=user.name or user.email or "Unknown",
        )
        if job.notification_id is None:
            try:
                await ensure_platform_job_notification(db, job)
                await db.commit()
                await db.refresh(job)
            except Exception:
                logger.warning(
                    "Workspace release lock-in queued without notification",
                    extra={"platform_job_id": str(job.id)},
                    exc_info=True,
                )
        await publish_platform_job_update(job)
        return result
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace release not found",
        ) from exc
    except WorkspaceReleaseActivationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception(
            "Workspace release is Live but history projection could not be queued",
            extra={"workspace_release_row_id": str(release_id)},
        )
        await db.rollback()
        return await WorkspaceReleaseActivationService(
            db, ctx.org_id
        ).mark_projection_queue_failed(release_id, str(exc))


@router.get("/live", response_model=WorkspaceLiveStatusResponse)
async def get_live_workspace_release(
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
) -> WorkspaceLiveStatusResponse:
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    return await WorkspaceReleaseActivationService(db, ctx.org_id).get_live()


@router.get(
    "/releases/{release_id}", response_model=WorkspaceReleaseStatusResponse
)
async def get_workspace_release_status(
    release_id: UUID,
    ctx: Context,
    db: DbSession,
    user: CurrentSuperuser,
) -> WorkspaceReleaseStatusResponse:
    if ctx.org_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an organization context is required",
        )
    try:
        return await WorkspaceReleaseActivationService(db, ctx.org_id).get_release(
            release_id
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace release not found",
        ) from exc
