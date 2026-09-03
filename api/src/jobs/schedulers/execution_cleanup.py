"""
Execution Cleanup Scheduler

Cleans up stuck workflow executions and stale autonomous agent runs that
remain in claimed or otherwise recoverably orphaned states for too long.

Runs every 5 minutes to find and timeout stuck executions.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, select, text

from src.core.database import get_session_factory
from src.core.pubsub import (
    publish_agent_run_update,
    publish_execution_update,
    publish_history_update,
)
from src.core.redis_client import get_redis_client
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models import Execution as ExecutionModel, ExecutionLog
from src.models.orm.executions import WorkflowExecutionAttempt as ExecutionAttempt
from src.models.orm.workflows import Workflow
from src.services.execution_attempts import transition_execution_attempt

logger = logging.getLogger(__name__)

# Timeout thresholds
SCHEDULED_TIMEOUT_MINUTES = 24 * 60  # Leave a full day for deferred recovery
RUNNING_TIMEOUT_MINUTES = 30  # If RUNNING for 30+ minutes, worker likely crashed
CANCELLING_TIMEOUT_MINUTES = 3  # If CANCELLING for 3+ minutes, worker failed to cancel
RESTART_ORPHAN_GRACE_SECONDS = 120
DEFAULT_AGENT_RUN_TIMEOUT_SECONDS = 30 * 60
AGENT_RUN_TIMEOUT_GRACE_SECONDS = 5 * 60


def _restart_orphan_grace_seconds() -> int:
    try:
        return max(
            1,
            int(
                os.environ.get(
                    "BIFROST_WORKFLOW_RESTART_ORPHAN_GRACE_SECONDS",
                    str(RESTART_ORPHAN_GRACE_SECONDS),
                )
            ),
        )
    except ValueError:
        return RESTART_ORPHAN_GRACE_SECONDS


async def _recover_restart_orphan(db, execution: ExecutionModel) -> bool:
    """Republish a fenced RUNNING execution after its worker disappears."""
    attempt_count = await db.scalar(
        select(func.count(ExecutionAttempt.id)).where(
            ExecutionAttempt.execution_id == execution.id
        )
    )
    from src.services.execution.retry_policy import (
        operator_max_attempts,
        should_retry_execution,
    )

    if not should_retry_execution(
        getattr(execution, "retry_policy", None),
        "worker_lost",
        int(attempt_count or 0),
        operator_max_attempts(),
    ):
        return False

    from src.services.execution.async_executor import (
        republish_execution_from_dispatch,
    )
    from src.services.execution.attempts import finalize_attempt

    active_attempt = await db.scalar(
        select(ExecutionAttempt)
        .where(
            ExecutionAttempt.execution_id == execution.id,
            ExecutionAttempt.completed_at.is_(None),
        )
        .with_for_update()
    )
    if active_attempt is None or active_attempt.claim_token is None:
        return False

    # Publish while the current database projection is still untouched. The
    # execution advisory lock prevents the consumer from claiming this message
    # until the caller commits PENDING. If publication fails, no attempt or
    # execution state has been changed and a later sweep can retry safely.
    await republish_execution_from_dispatch(execution)
    accepted = await finalize_attempt(
        db,
        execution.id,
        active_attempt.claim_token,
        status="worker_lost",
        phase="terminal",
        failure_code="worker_lost",
        failure_phase="worker",
    )
    if not accepted:
        return False

    await transition_execution_attempt(
        db,
        logical_job_type="workflow_execution",
        logical_job_id=execution.id,
        status="worker_lost",
        failure_code="worker_lost",
        failure_message="Worker heartbeat disappeared before completion.",
    )
    execution.status = "Pending"
    execution.started_at = None
    execution.completed_at = None
    execution.duration_ms = None
    execution.error_message = None
    return True


def _execution_age_anchor(execution: ExecutionModel) -> datetime:
    """Return the timestamp that represents when active work became due."""
    anchor = execution.started_at or execution.scheduled_at or execution.created_at
    if anchor.tzinfo is None:
        return anchor.replace(tzinfo=timezone.utc)
    return anchor.astimezone(timezone.utc)


def _is_restart_orphan(
    execution: ExecutionModel,
    *,
    now: datetime,
    active_attempt: ExecutionAttempt | None = None,
) -> bool:
    """Return whether the authoritative database attempt lease expired."""
    if (
        not execution.started_at
        or active_attempt is None
        or active_attempt.claim_token is None
        or active_attempt.status not in {"claimed", "running"}
    ):
        return False
    lease_renewed_at = active_attempt.heartbeat_at or active_attempt.claimed_at
    if lease_renewed_at is None:
        return False
    if lease_renewed_at.tzinfo is None:
        lease_renewed_at = lease_renewed_at.replace(tzinfo=timezone.utc)
    return (now - lease_renewed_at).total_seconds() >= _restart_orphan_grace_seconds()


async def cleanup_stuck_executions() -> dict[str, Any]:
    """
    Clean up stuck executions.

    Finds executions that have been stuck after worker claim or cancellation
    and marks them as TIMEOUT/CANCELLED. PENDING rows remain queued: age alone
    is not evidence that their broker delivery was lost.

    Returns:
        Summary of cleanup results
    """
    logger.info("Starting execution cleanup")

    from src.models.enums import ExecutionStatus

    results = {
        "scheduled_timeouts": 0,
        "pending_timeouts": 0,
        "running_timeouts": 0,
        "runner_loss_recoveries": 0,
        "cancelling_timeouts": 0,
        "total_cleaned": 0,
        "errors": [],
        "agent_run_queued_timeouts": 0,
        "agent_run_running_timeouts": 0,
        "agent_run_total_cleaned": 0,
        "agent_run_errors": [],
    }

    now = datetime.now(timezone.utc)

    try:
        # Collect data for WebSocket broadcasts (published after session closes)
        pubsub_updates: list[dict] = []

        session_factory = get_session_factory()
        async with session_factory() as db:
            # A SCHEDULED row can be created well before its due time. Only
            # consider it orphaned after scheduled_at, falling back to
            # created_at for immediate rows that never received a due time.
            scheduled_cutoff = now - timedelta(minutes=SCHEDULED_TIMEOUT_MINUTES)
            scheduled_result = await db.execute(
                select(ExecutionModel).where(
                    and_(
                        ExecutionModel.status == ExecutionStatus.SCHEDULED.value,
                        func.coalesce(
                            ExecutionModel.scheduled_at,
                            ExecutionModel.created_at,
                        )
                        < scheduled_cutoff,
                    )
                )
            )
            scheduled_stuck = list(scheduled_result.scalars().all())

            # Find stuck RUNNING executions — respect per-workflow timeout
            # Join with Workflow to get configured timeout_seconds.
            # Use workflow timeout + 5 min grace (process pool should kill first).
            # Fallback to RUNNING_TIMEOUT_MINUTES if no workflow found.
            running_result = await db.execute(
                select(
                    ExecutionModel,
                    Workflow.timeout_seconds,
                    ExecutionAttempt,
                ).where(
                    and_(
                        ExecutionModel.status == ExecutionStatus.RUNNING.value,
                    )
                )
                .outerjoin(Workflow, ExecutionModel.workflow_id == Workflow.id)
                .outerjoin(
                    ExecutionAttempt,
                    and_(
                        ExecutionAttempt.execution_id == ExecutionModel.id,
                        ExecutionAttempt.completed_at.is_(None),
                    ),
                )
            )
            running_stuck = []
            for execution, wf_timeout, active_attempt in running_result.all():
                if _is_restart_orphan(
                    execution,
                    now=now,
                    active_attempt=active_attempt,
                ):
                    running_stuck.append(execution)
                    continue
                age_anchor = _execution_age_anchor(execution)
                if execution.started_at is None:
                    if (now - age_anchor).total_seconds() > RUNNING_TIMEOUT_MINUTES * 60:
                        running_stuck.append(execution)
                    continue
                # timeout_seconds == 0 means no timeout — skip entirely
                if wf_timeout is not None and wf_timeout == 0:
                    continue
                # Use per-workflow timeout + 5 min grace, or fallback
                effective_timeout_s = (wf_timeout + 300) if wf_timeout else (RUNNING_TIMEOUT_MINUTES * 60)
                elapsed = (now - age_anchor).total_seconds()
                if elapsed > effective_timeout_s:
                    running_stuck.append(execution)

            # Find stuck CANCELLING executions
            cancelling_cutoff = now - timedelta(minutes=CANCELLING_TIMEOUT_MINUTES)
            cancelling_result = await db.execute(
                select(ExecutionModel).where(
                    and_(
                        ExecutionModel.status == ExecutionStatus.CANCELLING.value,
                        func.coalesce(
                            ExecutionModel.started_at,
                            ExecutionModel.scheduled_at,
                            ExecutionModel.created_at,
                        )
                        < cancelling_cutoff,
                    )
                )
            )
            cancelling_stuck = list(cancelling_result.scalars().all())

            all_stuck = (
                scheduled_stuck
                + running_stuck
                + cancelling_stuck
            )
            logger.info(f"Found {len(all_stuck)} stuck executions to clean up")

            for execution in all_stuck:
                try:
                    # Candidate discovery is intentionally lock-free. Re-fence
                    # and re-read before mutating so a result committed after
                    # discovery cannot be overwritten by this sweep. Keep the
                    # global order execution advisory lock -> execution row ->
                    # attempt row, matching result and cancellation paths.
                    await db.execute(
                        text(
                            "SELECT pg_advisory_xact_lock("
                            "hashtext('bifrost:workflow-execution:' || :execution_id))"
                        ),
                        {"execution_id": str(execution.id)},
                    )
                    execution = await db.scalar(
                        select(ExecutionModel)
                        .where(ExecutionModel.id == execution.id)
                        .execution_options(populate_existing=True)
                        .with_for_update()
                    )
                    if execution is None or execution.status not in {
                        ExecutionStatus.SCHEDULED.value,
                        ExecutionStatus.PENDING.value,
                        ExecutionStatus.RUNNING.value,
                        ExecutionStatus.CANCELLING.value,
                    }:
                        continue

                    # Status alone is insufficient: the claimant may have
                    # refreshed the row after candidate discovery. Reapply the
                    # status-specific age predicate while holding the fence.
                    locked_anchor = _execution_age_anchor(execution)
                    if (
                        execution.status == ExecutionStatus.SCHEDULED.value
                        and locked_anchor >= scheduled_cutoff
                    ):
                        continue
                    # PENDING rows are not cleanup candidates in this sweep.
                    # Reaching it here means publication won after discovery.
                    if execution.status == ExecutionStatus.PENDING.value:
                        continue
                    if (
                        execution.status == ExecutionStatus.CANCELLING.value
                        and locked_anchor >= cancelling_cutoff
                    ):
                        continue
                    if execution.status == ExecutionStatus.RUNNING.value:
                        active_attempt = await db.scalar(
                            select(ExecutionAttempt)
                            .where(
                                ExecutionAttempt.execution_id == execution.id,
                                ExecutionAttempt.completed_at.is_(None),
                            )
                            .with_for_update()
                        )
                        restart_orphan = _is_restart_orphan(
                            execution,
                            now=now,
                            active_attempt=active_attempt,
                        )
                        if not restart_orphan:
                            workflow_timeout = await db.scalar(
                                select(Workflow.timeout_seconds).where(
                                    Workflow.id == execution.workflow_id
                                )
                            )
                            if workflow_timeout == 0:
                                continue
                            effective_timeout_s = (
                                workflow_timeout + 300
                                if workflow_timeout
                                else RUNNING_TIMEOUT_MINUTES * 60
                            )
                            if (
                                now - locked_anchor
                            ).total_seconds() <= effective_timeout_s:
                                continue

                        if restart_orphan and await _recover_restart_orphan(
                            db, execution
                        ):
                            results["runner_loss_recoveries"] += 1
                            await db.commit()
                            continue

                    # Determine timeout reason and final status
                    original_status = ExecutionStatus(execution.status)
                    age_anchor = _execution_age_anchor(execution)

                    if execution.status == ExecutionStatus.SCHEDULED.value:
                        timeout_reason = (
                            f"SCHEDULED execution was not enqueued within "
                            f"{SCHEDULED_TIMEOUT_MINUTES}+ minutes of its due time."
                        )
                        final_status = ExecutionStatus.FAILED
                        results["scheduled_timeouts"] += 1

                    elif execution.status == ExecutionStatus.RUNNING.value:
                        elapsed_min = int((now - age_anchor).total_seconds() / 60)
                        if restart_orphan:
                            timeout_reason = (
                                f"Stuck in RUNNING status for {elapsed_min}+ minutes. "
                                "Execution predates all current worker heartbeats and is not claimed by any live worker."
                            )
                        else:
                            timeout_reason = (
                                f"Stuck in RUNNING status for {elapsed_min}+ minutes. "
                                "Likely worker crash or workflow hang."
                            )
                        final_status = ExecutionStatus.TIMEOUT
                        results["running_timeouts"] += 1

                    elif execution.status == ExecutionStatus.CANCELLING.value:
                        timeout_reason = (
                            f"Stuck in CANCELLING status for {CANCELLING_TIMEOUT_MINUTES}+ minutes. "
                            "Worker likely crashed during cancellation."
                        )
                        final_status = ExecutionStatus.CANCELLED
                        results["cancelling_timeouts"] += 1

                    else:
                        continue

                    # Log orphan execution being swept (before status update, to capture original status)
                    stuck_for_seconds = int((now - age_anchor).total_seconds())
                    logger.warning(
                        "orphan_execution_swept",
                        extra={
                            "execution_id": str(execution.id),
                            "stuck_status": execution.status,
                            "stuck_for_seconds": stuck_for_seconds,
                        },
                    )

                    logger.warning(
                        f"Timing out stuck execution: {execution.id}",
                        extra={
                            "execution_id": str(execution.id),
                            "workflow_name": execution.workflow_name,
                            "status": execution.status,
                            "timeout_reason": timeout_reason,
                        },
                    )

                    # Revoke the current attempt before changing the logical
                    # projection. A delayed child callback carrying this
                    # attempt's token will then be rejected as stale.
                    if original_status != ExecutionStatus.RUNNING:
                        active_attempt = await db.scalar(
                            select(ExecutionAttempt)
                            .where(
                                ExecutionAttempt.execution_id == execution.id,
                                ExecutionAttempt.completed_at.is_(None),
                            )
                            .with_for_update()
                        )
                    if active_attempt is not None:
                        if original_status == ExecutionStatus.CANCELLING:
                            active_attempt.status = "cancelled"
                            active_attempt.failure_code = "cancellation_timeout"
                            active_attempt.failure_phase = "cancellation"
                        elif original_status == ExecutionStatus.SCHEDULED:
                            active_attempt.status = "failed"
                            active_attempt.failure_code = (
                                "dispatch_publication_timeout"
                            )
                            active_attempt.failure_phase = "dispatch"
                        elif original_status == ExecutionStatus.PENDING:
                            active_attempt.status = "timed_out"
                            active_attempt.failure_code = "queue_timeout"
                            active_attempt.failure_phase = "queue"
                        elif (
                            original_status == ExecutionStatus.RUNNING
                            and restart_orphan
                        ):
                            active_attempt.status = "worker_lost"
                            active_attempt.failure_code = "worker_incarnation_lost"
                            active_attempt.failure_phase = "worker"
                        else:
                            active_attempt.status = "timed_out"
                            active_attempt.failure_code = "stuck_execution_timeout"
                            active_attempt.failure_phase = "execution"
                        active_attempt.phase = "terminal"
                        active_attempt.completed_at = now
                        active_attempt.heartbeat_at = now

                    # Update execution
                    execution.status = final_status.value  # type: ignore[assignment]
                    execution.error_message = timeout_reason
                    execution.completed_at = now
                    if original_status in {
                        ExecutionStatus.RUNNING,
                        ExecutionStatus.CANCELLING,
                    }:
                        await transition_execution_attempt(
                            db,
                            logical_job_type="workflow_execution",
                            logical_job_id=execution.id,
                            status=(
                                "cancelled"
                                if final_status == ExecutionStatus.CANCELLED
                                else "worker_lost"
                            ),
                            failure_code="automatic_cleanup",
                            failure_message=timeout_reason,
                        )

                    # Add timeout log entry
                    log_entry = ExecutionLog(
                        execution_id=execution.id,
                        level="error",
                        message=timeout_reason,
                        log_metadata={
                            "timeout_type": "automatic_cleanup",
                            "original_status": original_status.value,
                            "age_anchor": age_anchor.isoformat(),
                        },
                        timestamp=now,
                    )
                    db.add(log_entry)

                    results["total_cleaned"] += 1

                    # Collect data for pubsub (published after session closes)
                    pubsub_updates.append({
                        "execution_id": str(execution.id),
                        "final_status": final_status.value,
                        "timeout_reason": timeout_reason,
                        "executed_by": execution.executed_by,
                        "executed_by_name": execution.executed_by_name,
                        "workflow_name": execution.workflow_name,
                        "org_id": execution.organization_id,
                        "started_at": execution.started_at,
                    })

                except Exception as e:
                    logger.error(
                        f"Error processing execution cleanup for {execution.id}",
                        extra={"error": str(e)},
                        exc_info=True,
                    )
                    results["errors"].append({
                        "execution_id": str(execution.id),
                        "error": str(e),
                    })

            # Commit all changes
            await db.commit()

        # Remove transient queue/context state after the terminal DB commit.
        # Redis may be the failed dependency, so each cleanup remains
        # best-effort and the next scheduler pass can converge it.
        from src.services.execution.queue_tracker import remove_from_queue

        redis_client = get_redis_client()
        for update in pubsub_updates:
            try:
                await remove_from_queue(update["execution_id"])
                await redis_client.delete_pending_execution(update["execution_id"])
            except Exception as e:
                logger.warning(
                    "Durable execution cleanup completed but transient state cleanup failed for %s: %s",
                    update["execution_id"],
                    e,
                )

        # Publish WebSocket updates AFTER session is closed (no DB connection held)
        for update in pubsub_updates:
            try:
                await publish_execution_update(
                    update["execution_id"],
                    update["final_status"],
                    {"error": update["timeout_reason"]},
                )
                await publish_history_update(
                    execution_id=update["execution_id"],
                    status=update["final_status"],
                    executed_by=update["executed_by"],
                    executed_by_name=update["executed_by_name"],
                    workflow_name=update["workflow_name"],
                    org_id=update["org_id"],
                    started_at=update["started_at"],
                    completed_at=now,
                )
            except Exception as e:
                logger.warning(f"Failed to publish update for {update['execution_id']}: {e}")

        logger.info(
            "Execution cleanup completed",
            extra={
                "pending_timeouts": results["pending_timeouts"],
                "scheduled_timeouts": results["scheduled_timeouts"],
                "running_timeouts": results["running_timeouts"],
                "cancelling_timeouts": results["cancelling_timeouts"],
                "total_cleaned": results["total_cleaned"],
            },
        )

    except Exception as e:
        logger.error("Error in execution cleanup", extra={"error": str(e)}, exc_info=True)
        results["errors"].append({"error": str(e)})
    finally:
        try:
            agent_run_results = await _cleanup_stale_agent_runs(now)
            results.update(agent_run_results)
        except Exception as e:
            logger.error("Error in agent run cleanup", extra={"error": str(e)}, exc_info=True)
            results["agent_run_errors"].append({"error": str(e)})
        else:
            logger.info(
                "Agent run cleanup completed",
                extra={
                    "agent_run_queued_timeouts": results["agent_run_queued_timeouts"],
                    "agent_run_running_timeouts": results["agent_run_running_timeouts"],
                    "agent_run_total_cleaned": results["agent_run_total_cleaned"],
                },
            )

    return results


def _agent_run_timeout_seconds(agent: Agent | None) -> int:
    """Return the configured timeout for an agent, falling back to the shared default."""
    configured = getattr(agent, "max_run_timeout", None)
    if configured is not None and configured > 0:
        return configured
    return DEFAULT_AGENT_RUN_TIMEOUT_SECONDS


async def _cleanup_stale_agent_runs(now: datetime) -> dict[str, Any]:
    """Terminalize stale queued/running AgentRun rows without replaying them."""
    session_factory = get_session_factory()
    updates: list[dict[str, Any]] = []
    results: dict[str, Any] = {
        "agent_run_queued_timeouts": 0,
        "agent_run_running_timeouts": 0,
        "agent_run_total_cleaned": 0,
        "agent_run_errors": [],
    }

    try:
        async with session_factory() as db:
            query = (
                select(AgentRun, Agent)
                .join(Agent, AgentRun.agent_id == Agent.id)
                .where(AgentRun.status.in_(("queued", "running")))
                .order_by(AgentRun.created_at.asc())
            )
            runs = (await db.execute(query)).all()

            for candidate, agent in runs:
                timeout_seconds = _agent_run_timeout_seconds(agent)
                timeout_with_grace = timeout_seconds + AGENT_RUN_TIMEOUT_GRACE_SECONDS
                reference_time = (
                    candidate.created_at
                    if candidate.status == "queued"
                    else (candidate.started_at or candidate.created_at)
                )
                if reference_time is None:
                    continue

                elapsed = (now - reference_time).total_seconds()
                if elapsed <= timeout_with_grace:
                    continue

                # Lock only the row already identified as stale. The status
                # predicate is a compare-and-set guard against a worker that
                # completed between the candidate read and this lock.
                run = (
                    await db.execute(
                        select(AgentRun)
                        .where(
                            AgentRun.id == candidate.id,
                            AgentRun.status == candidate.status,
                        )
                        .with_for_update(skip_locked=True, of=AgentRun)
                    )
                ).scalar_one_or_none()
                if run is None:
                    continue

                reference_time = (
                    run.created_at
                    if run.status == "queued"
                    else (run.started_at or run.created_at)
                )
                if reference_time is None:
                    continue
                elapsed = (now - reference_time).total_seconds()
                if elapsed <= timeout_with_grace:
                    continue

                agent_name = agent.name
                if run.status == "queued":
                    final_status = "failed"
                    timeout_reason = (
                        f"Agent run timed out waiting in queue after "
                        f"{timeout_with_grace} seconds."
                    )
                    results["agent_run_queued_timeouts"] += 1
                else:
                    final_status = "timeout"
                    timeout_reason = (
                        f"Agent run timed out after {timeout_with_grace} seconds."
                    )
                    results["agent_run_running_timeouts"] += 1

                logger.warning(
                    "agent_run_swept",
                    extra={
                        "agent_run_id": str(run.id),
                        "agent_id": str(run.agent_id),
                        "agent_name": agent_name,
                        "stuck_status": run.status,
                        "stuck_for_seconds": int(elapsed),
                        "timeout_seconds": timeout_seconds,
                        "timeout_with_grace": timeout_with_grace,
                    },
                )

                run.status = final_status
                run.error = timeout_reason
                run.completed_at = now
                updates.append(
                    {
                        "run": run,
                        "agent_name": agent_name,
                    }
                )
                results["agent_run_total_cleaned"] += 1

            await db.commit()

        for update in updates:
            try:
                await publish_agent_run_update(update["run"], update["agent_name"])
            except Exception:
                logger.warning(
                    "Failed to publish agent run update",
                    extra={"agent_run_id": str(update["run"].id)},
                    exc_info=True,
                )

        return results
    except Exception as e:
        logger.error("Error in agent run cleanup", extra={"error": str(e)}, exc_info=True)
        results["agent_run_errors"].append({"error": str(e)})
        return results
