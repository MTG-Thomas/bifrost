"""Preview-only rapid Workspace promotion HTTP surface."""

from fastapi import APIRouter, HTTPException, status

from src.config import get_settings
from src.core.auth import Context, CurrentSuperuser
from src.core.db_deps import DbSession
from src.models.contracts.workspace_promotions import (
    WorkspacePromotionPreviewRequest,
    WorkspacePromotionPreviewResponse,
)
from src.services.workspace_promotions import (
    WorkspacePromotionInvalid,
    WorkspacePromotionPreviewService,
)

router = APIRouter(
    prefix="/api/workspace-promotions",
    tags=["Workspace rapid promotion (preview only)"],
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
        return await WorkspacePromotionPreviewService(db, ctx.org_id).preview(
            request, user.user_id
        )
    except WorkspacePromotionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
