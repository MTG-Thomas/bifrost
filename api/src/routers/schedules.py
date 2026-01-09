"""
Schedules Router

Lists scheduled workflows from the database with enriched schedule metadata.

Note: Schedules are read from the workflows database table (schedule column)
which is populated when workflow files are written via the API or git sync.

Manual triggering of scheduled workflows should use POST /api/workflows/execute.
Automatic scheduled execution is handled by the cron_scheduler job via RabbitMQ.
"""

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import desc, func, select

from src.models import ScheduleMetadata
from src.models import Workflow as WorkflowORM
from src.models.orm.executions import Execution
from src.core.auth import Context, CurrentSuperuser
from src.core.database import DbSession
from src.services.cron_parser import (
    calculate_next_run,
    cron_to_human_readable,
    is_cron_expression_valid,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/schedules", tags=["Schedules"])

# Minimum interval for schedules (5 minutes)
MIN_INTERVAL_SECONDS = 300


def _validate_cron(expression: str) -> tuple[Literal["valid", "warning", "error"], str | None]:
    """Validate a CRON expression and return status with optional message."""
    if not is_cron_expression_valid(expression):
        return "error", "Invalid CRON expression"

    # Check for too-frequent schedules (warning if < 5 minutes)
    try:
        from croniter import croniter
        now = datetime.utcnow()
        cron = croniter(expression, now)
        next1 = cron.get_next(datetime)
        next2 = cron.get_next(datetime)
        interval = (next2 - next1).total_seconds()

        if interval < MIN_INTERVAL_SECONDS:
            return "warning", f"Schedule runs more frequently than {MIN_INTERVAL_SECONDS // 60} minutes"
    except Exception:
        pass

    return "valid", None


# =============================================================================
# HTTP Endpoints
# =============================================================================


@router.get(
    "",
    response_model=list[ScheduleMetadata],
    summary="List scheduled workflows",
    description="List all workflows that have a schedule configured with enriched metadata (Platform admin only)",
)
async def list_schedules(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> list[ScheduleMetadata]:
    """
    List all scheduled workflows with enriched metadata.

    Returns workflows that have cron schedules configured, enriched with:
    - CRON validation status
    - Human-readable schedule description
    - Next run time calculation
    - Execution history (last run, count)
    """
    try:
        now = datetime.utcnow()

        # Query workflows with schedules from database
        query = (
            select(WorkflowORM)
            .where(WorkflowORM.is_active.is_(True))
            .where(WorkflowORM.schedule.isnot(None))
            .order_by(WorkflowORM.name)
        )
        result = await db.execute(query)
        workflows = result.scalars().all()

        # Get execution stats for all scheduled workflows in one query
        workflow_names = [w.name for w in workflows]
        if workflow_names:
            # Get latest execution and count per workflow
            exec_stats_query = (
                select(
                    Execution.workflow_name,
                    func.count(Execution.id).label("execution_count"),
                    func.max(Execution.completed_at).label("last_run_at"),
                )
                .where(Execution.workflow_name.in_(workflow_names))
                .group_by(Execution.workflow_name)
            )
            exec_stats_result = await db.execute(exec_stats_query)
            exec_stats = {
                row.workflow_name: {
                    "count": row.execution_count,
                    "last_run_at": row.last_run_at,
                }
                for row in exec_stats_result
            }

            # Get latest execution IDs
            latest_exec_query = (
                select(Execution.workflow_name, Execution.id)
                .where(Execution.workflow_name.in_(workflow_names))
                .order_by(Execution.workflow_name, desc(Execution.created_at))
                .distinct(Execution.workflow_name)
            )
            latest_exec_result = await db.execute(latest_exec_query)
            latest_exec_ids = {
                row.workflow_name: str(row.id) for row in latest_exec_result
            }
        else:
            exec_stats = {}
            latest_exec_ids = {}

        # Build enriched schedule metadata
        scheduled_workflows = []
        for workflow in workflows:
            cron_expr = workflow.schedule
            if not cron_expr:
                continue

            # Validate CRON expression
            validation_status, validation_message = _validate_cron(cron_expr)

            # Compute human-readable and next run
            human_readable = cron_to_human_readable(cron_expr)
            next_run_at = None
            is_overdue = False

            if validation_status != "error":
                try:
                    next_run_at = calculate_next_run(cron_expr, now)
                    is_overdue = next_run_at <= now
                except Exception:
                    pass

            # Get execution history
            stats = exec_stats.get(workflow.name, {"count": 0, "last_run_at": None})

            metadata = ScheduleMetadata(
                # Core workflow fields
                id=str(workflow.id),
                name=workflow.name,
                description=workflow.description,
                category=workflow.category or "General",
                tags=workflow.tags or [],
                schedule=cron_expr,
                source_file_path=workflow.path,
                relative_file_path=f"/workspace/{workflow.path}" if workflow.path else None,
                # Schedule validation
                validation_status=validation_status,
                validation_message=validation_message,
                # Computed schedule fields
                human_readable=human_readable,
                next_run_at=next_run_at,
                is_overdue=is_overdue,
                # Execution history
                last_run_at=stats["last_run_at"],
                last_execution_id=latest_exec_ids.get(workflow.name),
                execution_count=stats["count"],
            )
            scheduled_workflows.append(metadata)

        logger.info(f"Listed {len(scheduled_workflows)} scheduled workflows with enriched metadata")
        return scheduled_workflows

    except Exception as e:
        logger.error(f"Error listing schedules: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list schedules",
        )
