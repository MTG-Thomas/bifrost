"""Immutable Workspace promotion preview and draft-canary HTTP surface."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from src.config import get_settings
from src.core.auth import Context, CurrentSuperuser
from src.core.db_deps import DbSession
from src.models.contracts.workspace_promotions import (
    WorkspaceDraftCanaryAccepted,
    WorkspaceDraftCanaryRequest,
    WorkspacePromotionPreviewRequest,
    WorkspacePromotionPreviewResponse,
    WorkspacePromotionArtifactResponse,
)
from src.services.workspace_draft_canary import (
    WorkspaceDraftCanaryError,
    WorkspaceDraftCanaryService,
)
from src.services.workspace_promotions import (
    WorkspacePromotionInvalid,
    WorkspacePromotionPreviewService,
)

router = APIRouter(
    prefix="/api/workspace-promotions",
    tags=["Workspace rapid promotion"],
)


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
    response_model=WorkspaceDraftCanaryAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def execute_workspace_draft_canary(
    artifact_id: UUID,
    request: WorkspaceDraftCanaryRequest,
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
    return WorkspaceDraftCanaryAccepted(
        execution_id=execution_id,
        artifact_id=artifact_id,
    )
