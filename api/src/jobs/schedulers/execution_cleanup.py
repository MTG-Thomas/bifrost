"""
Execution Cleanup Scheduler

Cleans up stuck workflow executions and stale autonomous agent runs that
remain in claimed or otherwise recoverably orphaned states for too long.

Runs every 5 minutes to find and timeout stuck executions.
"""

import json
import logging
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


def _execution_age_anchor(execution: ExecutionModel) -> datetime:
    """Return the timestamp that represents when active work became due."""
    anchor = execution.started_at or execution.scheduled_at or execution.created_at
    if anchor.tzinfo is None:
        return anchor.replace(tzinfo=timezone.utc)
    return anchor.astimezone(timezone.utc)


async def _load_worker_heartbeat_state(now: datetime) -> dict[str, Any]:
    """Read Redis worker heartbeats for restart-orphan detection."""
    state: dict[str, Any] = {
        "active_execution_ids": set(),
        "live_worker_incarnation_ids": set(),
        "oldest_worker_started_at": None,
        "heartbeat_count": 0,
    }
    redis_client = get_redis_client()
    if not redis_client:
        return state

    try:
        cursor = 0
        while True:
            cursor, keys = await redis_client.scan(
                cursor,
                match="bifrost:pool:*:heartbeat",
                count=100,
            )
            for key in keys:
                raw = await redis_client.get(key)
                if not raw:
                    continue
                try:
                    heartbeat = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                state["heartbeat_count"] += 1
                incarnation_id = heartbeat.get("worker_incarnation_id")
                if incarnation_id:
                    state["live_worker_incarnation_ids"].add(str(incarnation_id))
                started_at = _parse_heartbeat_time(heartbeat.get("started_at"))
                if started_at is not None:
                    oldest = state["oldest_worker_started_at"]
                    state["oldest_worker_started_at"] = (
                        started_at if oldest is None else min(oldest, started_at)
                    )
                for process in heartbeat.get("processes") or []:
                    execution = (
                        process.get("execution")
                        if isinstance(process, dict)
                        else None
                    )
                    execution_id = (
                        execution.get("execution_id")
                        if isinstance(execution, dict)
                        else None
                    )
                    if execution_id:
                        state["active_execution_ids"].add(str(execution_id))
            if cursor == 0:
                break
    except Exception as exc:
        logger.warning(
            "Worker heartbeat state is unavailable; continuing with age-based execution cleanup: %s",
            exc,
        )
        return {
            "active_execution_ids": set(),
            "live_worker_incarnation_ids": set(),
            "oldest_worker_started_at": None,
            "heartbeat_count": 0,
        }

    return state


def _parse_heartbeat_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_restart_orphan(
    execution: ExecutionModel,
    *,
    now: datetime,
    heartbeat_state: dict[str, Any],
) -> bool:
    if not execution.started_at:
        return False
    if heartbeat_state.get("heartbeat_count", 0) <= 0:
        return False
    if str(execution.id) in heartbeat_state.get("active_execution_ids", set()):
        return False

    oldest_worker_started_at = heartbeat_state.get("oldest_worker_started_at")
    if oldest_worker_started_at is None:
        return False

    started_at = execution.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    if started_at >= oldest_worker_started_at:
        return False
    return (now - oldest_worker_started_at).total_seconds() >= RESTART_ORPHAN_GRACE_SECONDS


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
        heartbeat_state = await _load_worker_heartbeat_state(now)

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
                select(ExecutionModel, Workflow.timeout_seconds).where(
                    and_(
                        ExecutionModel.status == ExecutionStatus.RUNNING.value,
                    )
                ).outerjoin(Workflow, ExecutionModel.workflow_id == Workflow.id)
            )
            running_stuck = []
            for execution, wf_timeout in running_result.all():
                if _is_restart_orphan(execution, now=now, heartbeat_state=heartbeat_state):
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
                        restart_orphan = _is_restart_orphan(
                            execution,
                            now=now,
                            heartbeat_state=heartbeat_state,
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
                        if _is_restart_orphan(execution, now=now, heartbeat_state=heartbeat_state):
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
                    active_attempt = await db.scalar(
                        select(ExecutionAttempt)
                        .where(
                            ExecutionAttempt.execution_id == execution.id,
                            ExecutionAttempt.completed_at.is_(None),
                        )
                        .with_for_update()
                    )
                    if active_attempt is not None:
                        owner_incarnation_lost = bool(
                            active_attempt.worker_incarnation_id
                            and heartbeat_state.get("heartbeat_count", 0) > 0
                            and str(active_attempt.worker_incarnation_id)
                            not in heartbeat_state.get(
                                "live_worker_incarnation_ids", set()
                            )
                        )
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
                            and (
                                owner_incarnation_lost
                                or _is_restart_orphan(
                                    execution,
                                    now=now,
                                    heartbeat_state=heartbeat_state,
                                )
                            )
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
