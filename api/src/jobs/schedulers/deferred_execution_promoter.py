"""Deferred execution promoter.

Every 60 seconds, moves due SCHEDULED executions onto RabbitMQ through the
same serialized, publisher-confirmed transition used by run-now dispatch.

Design notes:

- Each row remains SCHEDULED until broker confirmation. An execution-keyed
  advisory lock prevents a concurrent publisher from dispatching it twice.
- ``SELECT ... FOR UPDATE SKIP LOCKED`` keeps the job safe to run in
  parallel (multiple scheduler pods / APScheduler threads): each batch
  picks a disjoint set of rows.
- ``LIMIT 500`` bounds recovery bursts after an outage — if 10k rows
  matured while the promoter was down, they drain in controlled batches.
- ``user_email`` is intentionally an empty string: the Execution row does
  not persist the triggering user's email. The worker hydrates it from
  the User record keyed by ``executed_by``. ``startup=None`` for the same
  reason — startup results are per-session context and would be stale by
  the time a scheduled row matures.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from src.core.database import get_db_context
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.services.execution.async_executor import _publish_scheduled_once

logger = logging.getLogger(__name__)

BATCH_LIMIT = 500


async def promote_due_executions() -> tuple[int, int]:
    """Promote due SCHEDULED rows to PENDING and publish them.

    Returns:
        Tuple of (promoted_count, publish_failures).
    """
    promoted = 0
    failures = 0

    async with get_db_context() as db:
        result = await db.execute(
            select(Execution)
            .where(Execution.status == ExecutionStatus.SCHEDULED)
            .where(Execution.scheduled_at <= datetime.now(timezone.utc))
            .order_by(Execution.scheduled_at.asc())
            .limit(BATCH_LIMIT)
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars().all())

        if not rows:
            return 0, 0

        # Do not hold the batch row locks while waiting for broker confirms.
        # The per-row publisher reacquires the canonical advisory fence.
        await db.commit()

        for row in rows:
            try:
                published = await _publish_scheduled_once(
                    execution_id=str(row.id),
                    publish_kwargs={
                        "execution_id": str(row.id),
                        "workflow_id": str(row.workflow_id) if row.workflow_id else None,
                        "parameters": row.parameters or {},
                        "org_id": str(row.organization_id) if row.organization_id else None,
                        "user_id": str(row.executed_by) if row.executed_by else "",
                        "user_name": row.executed_by_name or "",
                        "user_email": "",
                        "form_id": str(row.form_id) if row.form_id else None,
                        "startup": None,
                        "form_inputs": {},
                        "embed": {},
                        "api_key_id": str(row.api_key_id) if row.api_key_id else None,
                        "sync": False,
                        "is_platform_admin": bool(
                            (row.execution_context or {}).get(
                                "is_platform_admin", False
                            )
                        ),
                        "is_provider_org": bool(
                            (row.execution_context or {}).get(
                                "is_provider_org", False
                            )
                        ),
                        "is_external": bool(
                            (row.execution_context or {}).get("is_external", False)
                        ),
                        "file_path": None,
                        "solution_deployment_id": (
                            str(row.solution_deployment_id)
                            if row.solution_deployment_id
                            else None
                        ),
                        "runtime_evidence": row.runtime_evidence,
                        "runtime_mode": row.runtime_mode,
                        "execution_record_exists": True,
                    },
                )
                promoted += int(published)
            except Exception:
                failures += 1
                logger.exception(
                    "deferred_execution_promoter: publish failed; row remains scheduled",
                    extra={"execution_id": str(row.id)},
                )

        logger.info(
            "deferred_execution_promoter tick complete",
            extra={"promoted": promoted, "failures": failures},
        )
        return promoted, failures
