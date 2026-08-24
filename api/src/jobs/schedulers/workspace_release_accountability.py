"""Mark overdue Workspace releases and notify platform administrators."""

import logging

from sqlalchemy import func, select

from src.core.database import get_db_context
from src.models.contracts.notifications import (
    NotificationCategory,
    NotificationCreate,
    NotificationStatus,
    NotificationUpdate,
)
from src.models.orm.workspace_promotions import (
    WorkspacePromotionRelease,
    WorkspaceSourceRelease,
)
from src.services.notification_service import get_notification_service
from src.services.workspace_source_releases import sweep_overdue_workspace_releases

logger = logging.getLogger(__name__)
NOTIFICATION_TITLE = "Workspace release attention required"


async def check_workspace_release_accountability() -> dict[str, object]:
    async with get_db_context() as db:
        transitioned = await sweep_overdue_workspace_releases(db)
        source_count = int(
            await db.scalar(
                select(func.count(WorkspaceSourceRelease.id)).where(
                    WorkspaceSourceRelease.disposition == "attention_required"
                )
            )
            or 0
        )
        history_count = int(
            await db.scalar(
                select(func.count(WorkspacePromotionRelease.id)).where(
                    WorkspacePromotionRelease.activation_state == "live",
                    WorkspacePromotionRelease.lock_state == "attention_required",
                )
            )
            or 0
        )
        await db.commit()

    total = source_count + history_count
    notifications = get_notification_service()
    existing = await notifications.find_admin_notification_by_title(
        title=NOTIFICATION_TITLE,
        category=NotificationCategory.SYSTEM,
    )
    if total and existing is None:
        await notifications.create_notification(
            user_id="system",
            request=NotificationCreate(
                category=NotificationCategory.SYSTEM,
                title=NOTIFICATION_TITLE,
                description=(
                    f"{source_count} reviewed source release(s) and "
                    f"{history_count} Live history projection(s) require attention"
                ),
                metadata={
                    "action": "workspace_release_accountability",
                    "source_release_count": source_count,
                    "history_release_count": history_count,
                    "source_release_ids": transitioned["source_release_ids"],
                    "workspace_release_ids": transitioned["workspace_release_ids"],
                },
            ),
            for_admins=True,
            initial_status=NotificationStatus.AWAITING_ACTION,
        )
    elif total and existing is not None:
        await notifications.update_notification(
            existing.id,
            NotificationUpdate(
                status=NotificationStatus.AWAITING_ACTION,
                description=(
                    f"{source_count} reviewed source release(s) and "
                    f"{history_count} Live history projection(s) require attention"
                ),
            ),
        )
    elif not total and existing is not None:
        await notifications.dismiss_notification(existing.id, user_id="system")

    if transitioned["source_release_ids"] or transitioned["workspace_release_ids"]:
        logger.error(
            "workspace_release_accountability_attention",
            extra={
                "source_release_ids": transitioned["source_release_ids"],
                "workspace_release_ids": transitioned["workspace_release_ids"],
                "source_release_count": source_count,
                "history_release_count": history_count,
            },
        )
    return {
        "source_release_count": source_count,
        "history_release_count": history_count,
        "transitioned": transitioned,
    }


__all__ = ["check_workspace_release_accountability"]
