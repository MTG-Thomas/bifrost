"""Durable immutable Workspace promotion preview job."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from src.core.database import get_db_context
from src.jobs.execution_policy import (
    WorkloadClass,
    platform_job_operations_policy,
)
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.models.contracts.workspace_promotions import WorkspacePromotionPreviewRequest
from src.services.workspace_promotions import (
    WorkspacePromotionInvalid,
    build_workspace_promotion_preview_service,
)

WORKSPACE_PROMOTION_PREVIEW_JOB_TYPE = "workspace.promotion.preview"


class WorkspacePromotionPreviewPayload(BaseModel):
    request: WorkspacePromotionPreviewRequest


async def run_workspace_promotion_preview(
    context: PlatformJobContext,
    raw_payload: BaseModel,
) -> dict[str, object]:
    payload = WorkspacePromotionPreviewPayload.model_validate(raw_payload)
    if context.organization_id is None:
        raise PlatformJobFailure(
            "workspace_promotion_preview_org_missing",
            "Workspace promotion preview requires an organization.",
        )
    try:
        async with get_db_context() as db:
            service = await build_workspace_promotion_preview_service(
                db, context.organization_id
            )
            preview = await service.preview(
                payload.request, UUID(context.requested_by_user_id)
            )
        await context.log(
            "info",
            "workspace_promotion_previewed",
            f"Workspace promotion candidate {preview.candidate_id} previewed",
        )
        return preview.model_dump(mode="json")
    except WorkspacePromotionInvalid as exc:
        raise PlatformJobFailure(
            "workspace_promotion_preview_invalid",
            str(exc),
            retryable=False,
        ) from exc
    except ValueError as exc:
        raise PlatformJobFailure(
            "workspace_promotion_preview_invalid",
            str(exc),
            retryable=False,
        ) from exc


WORKSPACE_PROMOTION_PREVIEW_DEFINITION = PlatformJobDefinition(
    job_type=WORKSPACE_PROMOTION_PREVIEW_JOB_TYPE,
    payload_version=1,
    payload_model=WorkspacePromotionPreviewPayload,
    handler=run_workspace_promotion_preview,
    policy=PlatformJobPolicy(
        timeout_seconds=15 * 60,
        max_attempts=2,
        max_concurrency=2,
        retry_on_runner_loss=True,
        min_memory_headroom_mb=512,
    ),
    operations_policy=platform_job_operations_policy(
        WORKSPACE_PROMOTION_PREVIEW_JOB_TYPE,
        workload_class=WorkloadClass.PLATFORM_INTERACTIVE,
    ),
)
