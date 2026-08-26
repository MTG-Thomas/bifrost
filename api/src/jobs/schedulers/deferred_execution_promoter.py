"""Deferred execution promoter.

Every 60 seconds, moves SCHEDULED executions whose ``scheduled_at`` has
matured onto the RabbitMQ workflow-executions queue by flipping them to
PENDING and calling the shared ``_publish_pending`` helper.

Design notes:

- Each row uses the same advisory transaction lock as immediate publication.
  The broker publish is confirmed before SCHEDULED advances to PENDING, so a
  scheduler crash cannot strand an unpublished PENDING row.
- Multiple schedulers may discover the same candidate, but the advisory lock
  plus row lock lets only the still-SCHEDULED owner publish it.
- ``LIMIT 500`` bounds catch-up bursts, and reported worker free slots reduce
  that bound when capacity is known. Redis diagnostics never replace the
  PostgreSQL schedule authority.
- ``user_email`` is intentionally an empty string: the Execution row does
  not persist the triggering user's email. The worker hydrates it from
  the User record keyed by ``executed_by``. ``startup=None`` for the same
  reason — startup results are per-session context and would be stale by
  the time a scheduled row matures.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text

from src.core.database import get_db_context
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.services.execution.async_executor import _publish_pending
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
        return BATCH_LIMIT
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
        return min(BATCH_LIMIT, available) if reports else BATCH_LIMIT
    except Exception:
        logger.warning("Unable to read worker capacity; using bounded fallback")
        return BATCH_LIMIT


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
        await db.commit()

        for execution_id in candidate_ids:
            try:
                async with get_db_context() as claim_db:
                    await claim_db.execute(
                        text(
                            "SELECT pg_advisory_xact_lock("
                            "hashtext('bifrost:workflow-execution:' || :execution_id))"
                        ),
                        {"execution_id": str(execution_id)},
                    )
                    row = (
                        await claim_db.execute(
                            select(Execution)
                            .where(
                                Execution.id == execution_id,
                                Execution.status == ExecutionStatus.SCHEDULED,
                                Execution.scheduled_at <= datetime.now(timezone.utc),
                            )
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        continue
                    execution_failure_checkpoint(FailurePoint.SCHEDULE_PUBLISH)
                    await _publish_pending(
                        execution_id=str(row.id),
                        workflow_id=str(row.workflow_id) if row.workflow_id else None,
                        parameters=row.parameters or {},
                        org_id=str(row.organization_id) if row.organization_id else None,
                        user_id=str(row.executed_by) if row.executed_by else "",
                        user_name=row.executed_by_name or "",
                        user_email="",
                        form_id=str(row.form_id) if row.form_id else None,
                        startup=None,
                        form_inputs={},
                        embed={},
                        api_key_id=str(row.api_key_id) if row.api_key_id else None,
                        sync=False,
                        is_platform_admin=bool(
                            (row.execution_context or {}).get(
                                "is_platform_admin", False
                            )
                        ),
                        is_provider_org=bool(
                            (row.execution_context or {}).get(
                                "is_provider_org", False
                            )
                        ),
                        is_external=bool(
                            (row.execution_context or {}).get("is_external", False)
                        ),
                        file_path=None,
                        solution_deployment_id=(
                            str(row.solution_deployment_id)
                            if row.solution_deployment_id
                            else None
                        ),
                        runtime_evidence=(row.execution_context or {}).get(
                            "runtime_evidence"
                        ),
                        runtime_mode=row.runtime_mode,
                        execution_record_exists=True,
                    )
                    row.status = ExecutionStatus.PENDING
                    row.started_at = None
                    await claim_db.commit()
                    promoted += 1
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
