"""Bounded retention for inert local Workspace draft uploads."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.models.orm.workspace_promotions import WorkspacePromotionArtifact
from src.services.workspace_promotion_storage import WorkspacePromotionArtifactStorage
from src.services.workspace_promotions import (
    acquire_workspace_promotion_artifact_lock,
)

logger = logging.getLogger(__name__)

WORKSPACE_DRAFT_CLEANUP_BATCH_SIZE = 100


async def delete_expired_workspace_draft_batch(
    db: AsyncSession,
    *,
    now: datetime,
    batch_size: int = WORKSPACE_DRAFT_CLEANUP_BATCH_SIZE,
    storage_factory: Callable[..., WorkspacePromotionArtifactStorage]
    = WorkspacePromotionArtifactStorage,
) -> tuple[int, int]:
    """Delete one locked batch of expired draft rows and their exact objects."""

    if not 1 <= batch_size <= WORKSPACE_DRAFT_CLEANUP_BATCH_SIZE:
        raise ValueError(
            f"batch_size must be between 1 and {WORKSPACE_DRAFT_CLEANUP_BATCH_SIZE}"
        )
    candidates = list(
        (
            await db.scalars(
                select(WorkspacePromotionArtifact)
                .where(
                    WorkspacePromotionArtifact.target_kind == "draft",
                    WorkspacePromotionArtifact.expires_at <= now,
                )
                .order_by(WorkspacePromotionArtifact.expires_at.asc())
                .limit(batch_size)
            )
        ).all()
    )
    if not candidates:
        await db.commit()
        return 0, 0
    organization_ids = sorted(
        {artifact.organization_id for artifact in candidates}, key=str
    )
    for organization_id in organization_ids:
        await acquire_workspace_promotion_artifact_lock(db, organization_id)
    candidate_ids = [artifact.id for artifact in candidates]
    rows = list(
        (
            await db.scalars(
                select(WorkspacePromotionArtifact)
                .where(
                    WorkspacePromotionArtifact.id.in_(candidate_ids),
                    WorkspacePromotionArtifact.target_kind == "draft",
                    WorkspacePromotionArtifact.expires_at <= now,
                )
                .order_by(WorkspacePromotionArtifact.expires_at.asc())
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    content_keys = sorted(
        {
            (artifact.organization_id, str(artifact.content_id))
            for artifact in rows
        },
        key=lambda item: (str(item[0]), item[1]),
    )
    for organization_id, content_id in content_keys:
        try:
            referenced = await db.scalar(
                select(WorkspacePromotionArtifact.id)
                .where(
                    WorkspacePromotionArtifact.organization_id == organization_id,
                    WorkspacePromotionArtifact.content_id == content_id,
                    WorkspacePromotionArtifact.id.not_in(
                        [artifact.id for artifact in rows]
                    ),
                )
                .limit(1)
            )
            if referenced is None:
                await storage_factory(
                    organization_id, content_id
                ).delete_expired_draft()
        except Exception:  # noqa: BLE001 - storage backends vary
            await db.rollback()
            logger.warning(
                "Expired Workspace draft cleanup retained rows after object failure",
                extra={
                    "workspace_promotion_artifact_organization_id": str(
                        organization_id
                    ),
                    "workspace_promotion_artifact_content_id": content_id,
                },
                exc_info=True,
            )
            return 0, 1
    for artifact in rows:
        await db.delete(artifact)
    await db.commit()
    return len(rows), 0


async def cleanup_expired_workspace_drafts() -> dict[str, Any]:
    """Run one bounded hourly retention batch."""

    async with get_db_context() as db:
        deleted, object_failures = await delete_expired_workspace_draft_batch(
            db, now=datetime.now(timezone.utc)
        )
    return {
        "drafts_deleted": deleted,
        "object_cleanup_failures": object_failures,
        "batch_limit": WORKSPACE_DRAFT_CLEANUP_BATCH_SIZE,
    }


__all__ = [
    "WORKSPACE_DRAFT_CLEANUP_BATCH_SIZE",
    "cleanup_expired_workspace_drafts",
    "delete_expired_workspace_draft_batch",
]
