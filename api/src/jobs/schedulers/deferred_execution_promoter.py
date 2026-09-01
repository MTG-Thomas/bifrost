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
from src.services.execution.fault_injection import (
    FailurePoint,
    execution_failure_checkpoint,
)

logger = logging.getLogger(__name__)

BATCH_LIMIT = 500


async def _capacity_aware_batch_limit() -> int:
    """Use reported free workflow slots without making Redis authoritative."""
    from src.core.redis_client import get_redis_client

    redis = get_redis_client()
    if not redis:
        raise RuntimeError("worker capacity is unavailable")
    try:
        cursor = 0
        available = 0
        reports = 0
        while True:
            cursor, keys = await redis.scan(
                cursor, match="bifrost:pool:*:heartbeat", count=100
            )
            for key in keys:
                import json

                raw = await redis.get(key)
                if not raw:
                    continue
                heartbeat = json.loads(raw)
                slots = heartbeat.get("available_slots")
                if slots is not None:
                    reports += 1
                    available += max(0, int(slots))
            if cursor == 0:
                break
        if not reports:
            raise RuntimeError("no valid worker capacity reports are available")
        return min(BATCH_LIMIT, available)
    except Exception as exc:
        logger.warning("Unable to read worker capacity; deferring promotion")
        raise RuntimeError("worker capacity could not be read") from exc


async def promote_due_executions() -> tuple[int, int]:
    """Promote due SCHEDULED rows to PENDING and publish them.

    Returns:
        Tuple of (promoted_count, publish_failures).
    """
    promoted = 0
    failures = 0
    batch_limit = await _capacity_aware_batch_limit()
    if batch_limit == 0:
        logger.info("deferred_execution_promoter: no reported worker capacity")
        return 0, 0

    async with get_db_context() as db:
        result = await db.execute(
            select(Execution.id)
            .where(Execution.status == ExecutionStatus.SCHEDULED)
            .where(Execution.scheduled_at <= datetime.now(timezone.utc))
            .order_by(Execution.scheduled_at.asc())
            .limit(batch_limit)
        )
        candidate_ids = list(result.scalars().all())

        if not candidate_ids:
            return 0, 0
        # Do not hold the batch row locks while waiting for broker confirms.
        # The per-row publisher reacquires the canonical advisory fence.
        await db.commit()

        for execution_id in candidate_ids:
            try:
                async with get_db_context() as row_db:
                    row = await row_db.get(Execution, execution_id)
                    if row is None:
                        continue
                    # Copy every value needed for publication while the ORM row
                    # is attached. The context manager may roll back/expire the
                    # session on exit; detached attributes are not a safe retry
                    # boundary.
                    publish_execution_id = str(row.id)
                    publish_kwargs = {
                        "execution_id": publish_execution_id,
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
                            (row.execution_context or {}).get("is_platform_admin", False)
                        ),
                        "is_provider_org": bool(
                            (row.execution_context or {}).get("is_provider_org", False)
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
                    }
                execution_failure_checkpoint(FailurePoint.SCHEDULE_PUBLISH)
                published = await _publish_scheduled_once(
                    execution_id=publish_execution_id,
                    publish_kwargs=publish_kwargs,
                )
                promoted += int(published)
            except Exception:
                failures += 1
                logger.exception(
                    "deferred_execution_promoter: publish failed; row remains scheduled",
                    extra={"execution_id": str(execution_id)},
                )

        logger.info(
            "deferred_execution_promoter tick complete",
            extra={"promoted": promoted, "failures": failures},
        )
        return promoted, failures
