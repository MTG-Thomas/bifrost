"""
Workflow Execution Consumer

Processes async workflow executions from RabbitMQ queue.

Architecture (Redis-first):
1. API stores pending execution in Redis, publishes to RabbitMQ
2. Consumer reads pending execution from Redis
3. Consumer creates PostgreSQL record when starting
4. Consumer routes execution to ProcessPoolManager
5. ProcessPoolManager executes in worker process, returns result via callback
6. Consumer handles result: updates DB, flushes logs, cleans up Redis

For sync execution requests (sync=True in message):
- Pushes result to Redis after completion
- API waits on Redis BLPOP for the result

Execution Model:
- All executions use ProcessPoolManager (process isolation)
- Worker processes are pooled and reused for efficiency
- Timeouts and crashes are handled by the pool manager
"""

import asyncio
from dataclasses import replace
import logging
import time
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import select, text

from src.core.database import get_db_context
from src.core.pubsub import publish_execution_update, publish_history_update
from src.core.redis_client import get_redis_client
from src.jobs.execution_policy import broker_execution_policies
from src.jobs.rabbitmq import (
    BaseConsumer,
    DeliveryContext,
    DomainFailureHandled,
    DuplicateMessage,
    MalformedMessage,
    RetryableConsumerError,
)
from src.models.enums import ExecutionStatus
from src.repositories.executions import create_execution, update_execution
from src.services.execution.process_pool import ProcessPoolAdmissionRejected

logger = logging.getLogger(__name__)

# Queue name
QUEUE_NAME = "workflow-executions"

# Sync callers are already awake when derived terminal work begins. A short
# grace lets sibling authoritative result commits finish before shared daily
# and ROI aggregate rows are updated.
_SYNC_DERIVED_WORK_GRACE_SECONDS = 0.025
_CANCELLED_BEFORE_START_MESSAGE = "Execution was cancelled before it could start"


def _attempt_failure_phase(attempt_status: str, failure_code: str | None) -> str:
    """Map a terminal attempt outcome to its bounded failure phase."""
    if attempt_status == "worker_lost":
        return "worker"
    if attempt_status == "cancelled":
        return "cancellation"
    if failure_code == "result_persist_failed":
        return "result"
    return "execution"


def workflow_prefetch_count(settings: Any) -> int:
    """Cap workflow prefetch at local process admission capacity."""
    return max(1, min(settings.max_concurrency, settings.max_workers))


class WorkflowExecutionConsumer(BaseConsumer):
    """
    Consumer for workflow execution queue.

    Message format (minimal - context is in Redis):
    {
        "execution_id": "uuid",
        "workflow_id": "uuid" (optional, for workflow execution),
        "code": "base64-encoded-script" (optional, for inline scripts),
        "script_name": "name" (optional, for inline scripts),
        "sync": false (optional, if true pushes result to Redis for API)
    }

    Full execution context is read from Redis pending execution.
    """

    def __init__(self, *, queue_name: str = QUEUE_NAME):
        from src.config import get_settings
        from src.services.execution.process_pool import get_process_pool

        settings = get_settings()
        policy = broker_execution_policies()[QUEUE_NAME]
        if queue_name != QUEUE_NAME:
            policy = replace(policy, identifier=queue_name)
        self._workflow_operations_policy = policy
        super().__init__(
            queue_name=queue_name,
            prefetch_count=workflow_prefetch_count(settings),
            operations_policy=policy,
        )
        self._redis_client = get_redis_client()

        # Get the global ProcessPoolManager instance
        # This ensures package_install consumer can also update it
        self._pool = get_process_pool()
        # Set the result callback on the global pool
        self._pool.on_result = self._handle_result
        self._pool_started = False

    @staticmethod
    async def _lock_execution(session: Any, execution_id: str) -> None:
        """Acquire the canonical execution fence before any related row lock."""
        await session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('bifrost:workflow-execution:' || :execution_id))"
            ),
            {"execution_id": execution_id},
        )

    @staticmethod
    async def _run_derived_step(
        label: str,
        operation: Callable[[], Awaitable[Any]],
        *,
        attempts: int = 3,
    ) -> bool:
        """Retry terminal fan-out independently of durable finalization.

        Once PostgreSQL commits, a Redis/WebSocket failure must not cause the
        whole result callback to be replayed and mistaken for a stale result.
        Derived work is bounded, idempotent, and logged for reconciliation.
        """
        for attempt in range(attempts):
            try:
                await operation()
                return True
            except Exception as exc:
                logger.warning(
                    "Derived execution step %s failed (%s/%s): %s",
                    label,
                    attempt + 1,
                    attempts,
                    exc,
                )
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.1 * (2**attempt))
        return False

    async def start(self) -> None:
        """Start the process pool, then begin consuming messages.

        The pool must be fully ready (including the template process) before
        RabbitMQ can deliver work — otherwise route_execution would run while
        the template is still starting, and there would be no valid way to
        create worker processes for incoming messages.
        """
        await self._pool.start()
        self._pool_started = True
        logger.info("Process pool started")

        # Only now begin accepting messages from RabbitMQ.
        await super().start()

    async def stop(self) -> None:
        """Stop the consumer and process pool."""
        # Stop process pool
        if self._pool_started:
            await self._pool.stop()
            self._pool_started = False
            logger.info("Process pool stopped")

        # Call parent stop
        await super().stop()

    async def _handle_result(self, result: dict[str, Any]) -> None:
        """
        Handle result from process pool.

        This callback is invoked by the pool when a worker reports
        a result (success or failure, including timeouts and crashes).

        DB sessions are short-lived — Redis reads and pub/sub happen outside.
        """
        execution_id = result.get("execution_id", "")

        try:
            if result.get("success"):
                await self._process_success(execution_id, result)
            else:
                await self._process_failure(execution_id, result)
        except Exception as e:
            logger.error(f"Failed to process result for {execution_id}: {e}")
            raise

    async def _record_completion_metrics(
        self,
        *,
        workflow_id: str | None,
        org_id: str | None,
        status: str,
        duration_ms: int,
        peak_memory_bytes: int | None = None,
        cpu_total_seconds: float | None = None,
        time_saved: int = 0,
        value: float = 0.0,
        include_workflow_roi: bool = False,
    ) -> None:
        """Persist derived aggregates outside the authoritative result commit.

        Daily and workflow aggregates intentionally share rows across many
        executions. PostgreSQL serializes their atomic increments, so keeping
        them in the authoritative completion transaction makes concurrent sync
        callers wait behind unrelated aggregate bookkeeping. The execution
        record, buffered SDK writes, and captured logs are committed before
        this method is called.

        Aggregate failures have never been allowed to fail an execution. Keep
        that contract here while ensuring the short-lived metrics transaction
        is independently committed or rolled back.
        """
        from src.core.database import get_session_factory
        from src.core.metrics import update_daily_metrics, update_workflow_roi_daily

        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                await update_daily_metrics(
                    org_id=org_id,
                    status=status,
                    duration_ms=duration_ms,
                    peak_memory_bytes=peak_memory_bytes,
                    cpu_total_seconds=cpu_total_seconds,
                    time_saved=time_saved,
                    value=value,
                    workflow_id=workflow_id,
                    db=session,
                )

                if include_workflow_roi and workflow_id:
                    await update_workflow_roi_daily(
                        workflow_id=workflow_id,
                        org_id=org_id,
                        status=status,
                        time_saved=time_saved,
                        value=value,
                        db=session,
                    )

                await session.commit()
        except Exception as e:
            logger.warning(
                "Failed to update derived metrics for %s: %s",
                workflow_id or "inline execution",
                e,
                exc_info=True,
            )

    async def _process_success(
        self,
        execution_id: str,
        result: dict[str, Any],
    ) -> None:
        """
        Process a successful execution result.

        Updates the database, flushes logs, and publishes status updates.
        DB sessions are short-lived — Redis and pub/sub happen outside sessions.
        """
        from src.core.database import get_session_factory

        completion_started = time.perf_counter()
        workflow_result = result.get("result")
        duration_ms = result.get("duration_ms", 0)

        # Redis read — no DB connection held
        pending = await self._redis_client.get_pending_execution(execution_id)
        if not pending:
            logger.warning(f"No pending record found for result: {execution_id}")
            await self._terminalize_result_without_pending(result)
            return
        pending_read_ms = (time.perf_counter() - completion_started) * 1000

        workflow_id = pending.get("workflow_id")
        workflow_name = pending.get("workflow_name", "unknown")
        org_id = pending.get("org_id")
        user_id = pending.get("user_id")
        user_name = pending.get("user_name")
        is_sync = pending.get("sync", False)

        status_str = result.get("status", "Success")
        status = (
            ExecutionStatus(status_str)
            if status_str in [s.value for s in ExecutionStatus]
            else ExecutionStatus.SUCCESS
        )

        roi_data = result.get("roi") or {}
        roi_time_saved = roi_data.get("time_saved", 0)
        roi_value = roi_data.get("value", 0.0)

        # DB operations + flush — single short-lived session
        # (flush functions do Redis reads internally but DB writes share the session)
        session_factory = get_session_factory()
        async with session_factory() as session:
            await self._lock_execution(session, execution_id)
            attempt_token = result.get("attempt_token")
            if not attempt_token:
                from src.services.execution.attempts import has_recorded_attempt

                if await has_recorded_attempt(session, UUID(execution_id)):
                    raise RuntimeError(
                        "durable workflow result is missing its attempt fence"
                    )
            if attempt_token:
                from src.models.orm.executions import Execution
                from src.services.execution.attempts import finalize_attempt

                metrics_data = result.get("metrics") or {}
                execution_row = await session.scalar(
                    select(Execution)
                    .where(Execution.id == UUID(execution_id))
                    .with_for_update()
                )
                if execution_row is None or execution_row.status not in {
                    ExecutionStatus.RUNNING,
                    ExecutionStatus.CANCELLING,
                }:
                    logger.warning(
                        "Rejected stale workflow success projection for %s",
                        execution_id,
                    )
                    await session.rollback()
                    return
                attempt_status = (
                    "cancelled"
                    if execution_row.status == ExecutionStatus.CANCELLING
                    else "succeeded"
                )
                accepted = await finalize_attempt(
                    session,
                    UUID(execution_id),
                    UUID(str(attempt_token)),
                    status=attempt_status,
                    phase="terminal",
                    failure_code=(
                        "cancelled" if attempt_status == "cancelled" else None
                    ),
                    failure_phase=(
                        "cancellation" if attempt_status == "cancelled" else None
                    ),
                    duration_ms=duration_ms,
                    peak_memory_bytes=metrics_data.get("peak_memory_bytes"),
                    cpu_total_seconds=metrics_data.get("cpu_total_seconds"),
                )
                if not accepted:
                    logger.warning(
                        "Rejected stale workflow success result for %s", execution_id
                    )
                    await session.rollback()
                    return
            status = await update_execution(
                execution_id=execution_id,
                status=status,
                result=workflow_result,
                error_message=result.get("error"),
                error_type=result.get("error_type"),
                duration_ms=duration_ms,
                variables=result.get("variables"),
                execution_context=result.get("execution_context"),
                metrics=result.get("metrics"),
                time_saved=roi_time_saved,
                value=roi_value,
                session=session,
            )
            execution_update_ms = (time.perf_counter() - completion_started) * 1000
            if status == ExecutionStatus.CANCELLED:
                workflow_result = None
                roi_time_saved = 0
                roi_value = 0.0

            if pending.get("event") is not None:
                try:
                    from src.services.events.processor import update_delivery_from_execution
                    await update_delivery_from_execution(
                        execution_id, status.value, session=session
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to update event delivery for {execution_id[:8]}...: {e}"
                    )

            # Flush pending changes and logs (Redis read + DB write in same session)
            try:
                from bifrost._sync import flush_pending_changes
                changes_count = await flush_pending_changes(execution_id, session=session)
                if changes_count > 0:
                    logger.info(f"Flushed {changes_count} pending changes for {execution_id[:8]}...")
            except Exception as e:
                logger.warning(f"Failed to flush pending changes for {execution_id[:8]}...: {e}")
            changes_flushed_ms = (time.perf_counter() - completion_started) * 1000

            if result.get("logs"):
                try:
                    from bifrost._logging import flush_logs_to_postgres
                    logs_count = await flush_logs_to_postgres(
                        execution_id, session=session
                    )
                    if logs_count > 0:
                        logger.debug(
                            f"Flushed {logs_count} logs for {execution_id[:8]}..."
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to flush logs for {execution_id[:8]}...: {e}"
                    )
            logs_flushed_ms = (time.perf_counter() - completion_started) * 1000

            await session.commit()
        durable_ms = (time.perf_counter() - completion_started) * 1000

        # Wake sync callers as soon as their result and buffered SDK writes are
        # durably committed.  Everything below is terminal-event fan-out or
        # cleanup and must not add latency to the request/response path.
        sync_result_delivered = True
        if is_sync:
            sync_result_delivered = await self._run_derived_step(
                "sync-result",
                lambda: self._redis_client.push_result(
                    execution_id=execution_id,
                    status=status.value,
                    result=workflow_result,
                    error=result.get("error"),
                    error_type=result.get("error_type"),
                    duration_ms=duration_ms,
                ),
            )
        result_ready_ms = (time.perf_counter() - completion_started) * 1000

        if is_sync:
            await asyncio.sleep(_SYNC_DERIVED_WORK_GRACE_SECONDS)

        metrics_data = result.get("metrics") or {}
        await self._run_derived_step(
            "completion-metrics",
            lambda: self._record_completion_metrics(
                workflow_id=workflow_id,
                org_id=org_id,
                status=status.value,
                duration_ms=duration_ms,
                peak_memory_bytes=metrics_data.get("peak_memory_bytes"),
                cpu_total_seconds=metrics_data.get("cpu_total_seconds"),
                time_saved=roi_time_saved,
                value=roi_value,
                include_workflow_roi=True,
            ),
        )
        metrics_done_ms = (time.perf_counter() - completion_started) * 1000

        # Pub/sub — no DB connection held.  The result is already persisted and
        # execution clients fetch it from the result endpoint, so publishing it
        # again needlessly serializes and transports large payloads through
        # Redis and WebSocket connections.
        await self._run_derived_step(
            "execution-update",
            lambda: publish_execution_update(
                execution_id,
                status.value,
                {"duration_ms": duration_ms},
            ),
        )

        completed_at = datetime.now(timezone.utc)
        await self._run_derived_step(
            "history-update",
            lambda: publish_history_update(
                execution_id=execution_id,
                status=status.value,
                executed_by=user_id,
                executed_by_name=user_name,
                workflow_name=workflow_name,
                org_id=org_id,
                completed_at=completed_at,
                duration_ms=duration_ms,
            ),
        )

        # Redis cleanup — no DB connection held
        try:
            from src.core.cache import cleanup_execution_cache
            await cleanup_execution_cache(execution_id)
        except Exception as e:
            logger.warning(f"Failed to cleanup cache for {execution_id[:8]}...: {e}")

        if not is_sync or sync_result_delivered:
            await self._run_derived_step(
                "pending-cleanup",
                lambda: self._redis_client.delete_pending_execution(execution_id),
            )
        else:
            logger.error(
                "Sync result delivery exhausted for %s; retaining pending context",
                execution_id,
            )

        logger.debug(
            "Completion timing %s: pending=%.1fms execution_update=%.1fms "
            "changes=%.1fms logs=%.1fms durable=%.1fms result_ready=%.1fms "
            "metrics=%.1fms total=%.1fms",
            execution_id[:8],
            pending_read_ms,
            execution_update_ms,
            changes_flushed_ms,
            logs_flushed_ms,
            durable_ms,
            result_ready_ms,
            metrics_done_ms,
            (time.perf_counter() - completion_started) * 1000,
        )

        logger.info(
            f"Execution result processed: {execution_id[:8]}... status={status.value}",
            extra={
                "execution_id": execution_id,
                "workflow_id": workflow_id,
                "status": status.value,
                "duration_ms": duration_ms,
                "execution_model": "process",
            },
        )

    async def _process_failure(
        self,
        execution_id: str,
        result: dict[str, Any],
    ) -> None:
        """
        Process a failed execution result.

        Handles various failure types (timeout, crash, execution error).
        DB sessions are short-lived — Redis and pub/sub happen outside sessions.
        """
        from src.core.database import get_session_factory

        error = result.get("error", "Unknown error")
        error_type = result.get("error_type", "ExecutionError")
        duration_ms = result.get("duration_ms", 0)

        # Redis read — no DB connection held
        pending = await self._redis_client.get_pending_execution(execution_id)
        if not pending:
            logger.warning(f"No pending record found for failed result: {execution_id}")
            await self._terminalize_result_without_pending(result)
            return

        workflow_id = pending.get("workflow_id")
        workflow_name = pending.get("workflow_name", "unknown")
        org_id = pending.get("org_id")
        user_id = pending.get("user_id")
        user_email = pending.get("user_email")
        user_name = pending.get("user_name")
        is_sync = pending.get("sync", False)

        if error_type == "TimeoutError":
            status = ExecutionStatus.TIMEOUT
        elif error_type == "CancelledError":
            status = ExecutionStatus.CANCELLED
        else:
            status = ExecutionStatus.FAILED

        # DB operations + flush — single short-lived session
        session_factory = get_session_factory()
        async with session_factory() as session:
            await self._lock_execution(session, execution_id)
            attempt_token = result.get("attempt_token")
            if not attempt_token:
                from src.services.execution.attempts import has_recorded_attempt

                if await has_recorded_attempt(session, UUID(execution_id)):
                    raise RuntimeError(
                        "durable workflow result is missing its attempt fence"
                    )
            if attempt_token:
                from src.models.orm.executions import Execution
                from src.services.execution.attempts import (
                    failure_attempt_status,
                    finalize_attempt,
                )

                attempt_status, failure_code = failure_attempt_status(error_type)
                metrics_data = result.get("metrics") or {}
                execution_row = await session.scalar(
                    select(Execution)
                    .where(Execution.id == UUID(execution_id))
                    .with_for_update()
                )
                if execution_row is None or execution_row.status not in {
                    ExecutionStatus.RUNNING,
                    ExecutionStatus.CANCELLING,
                }:
                    logger.warning(
                        "Rejected stale workflow failure projection for %s",
                        execution_id,
                    )
                    await session.rollback()
                    return
                if execution_row.status == ExecutionStatus.CANCELLING:
                    attempt_status, failure_code = "cancelled", "cancelled"
                accepted = await finalize_attempt(
                    session,
                    UUID(execution_id),
                    UUID(str(attempt_token)),
                    status=attempt_status,
                    phase="terminal",
                    failure_code=failure_code,
                    failure_phase=_attempt_failure_phase(
                        attempt_status, failure_code
                    ),
                    duration_ms=duration_ms,
                    peak_memory_bytes=metrics_data.get("peak_memory_bytes"),
                    cpu_total_seconds=metrics_data.get("cpu_total_seconds"),
                )
                if not accepted:
                    logger.warning(
                        "Rejected stale workflow failure result for %s", execution_id
                    )
                    await session.rollback()
                    return
            status = await update_execution(
                execution_id=execution_id,
                status=status,
                error_message=error,
                error_type=error_type,
                duration_ms=duration_ms,
                session=session,
            )

            if pending.get("event") is not None:
                try:
                    from src.services.events.processor import update_delivery_from_execution
                    await update_delivery_from_execution(
                        execution_id,
                        status.value,
                        error_message=error,
                        session=session,
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to update event delivery for {execution_id[:8]}...: {e}"
                    )

            try:
                from bifrost._sync import flush_pending_changes
                changes_count = await flush_pending_changes(execution_id, session=session)
                if changes_count > 0:
                    logger.info(f"Flushed {changes_count} pending changes for failed {execution_id[:8]}...")
            except Exception as e:
                logger.warning(f"Failed to flush pending changes for {execution_id[:8]}...: {e}")

            if result.get("logs"):
                try:
                    from bifrost._logging import flush_logs_to_postgres
                    logs_count = await flush_logs_to_postgres(
                        execution_id, session=session
                    )
                    if logs_count > 0:
                        logger.debug(
                            f"Flushed {logs_count} logs for failed {execution_id[:8]}..."
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to flush logs for {execution_id[:8]}...: {e}"
                    )

            await session.commit()

        # A sync failure is just as latency-sensitive as a success. Wake the
        # caller once the authoritative failure is durable; aggregates and
        # terminal fan-out are derived follow-up work.
        sync_result_delivered = True
        if is_sync:
            sync_result_delivered = await self._run_derived_step(
                "sync-result",
                lambda: self._redis_client.push_result(
                    execution_id=execution_id,
                    status=status.value,
                    error=error,
                    error_type=error_type,
                    duration_ms=duration_ms,
                ),
            )

            await asyncio.sleep(_SYNC_DERIVED_WORK_GRACE_SECONDS)

        await self._run_derived_step(
            "completion-metrics",
            lambda: self._record_completion_metrics(
                workflow_id=workflow_id,
                org_id=org_id,
                status=status.value,
                duration_ms=duration_ms,
            ),
        )

        # Pub/sub — no DB connection held
        await self._run_derived_step(
            "execution-update",
            lambda: publish_execution_update(
                execution_id,
                status.value,
                {"error": error, "errorType": error_type},
            ),
        )

        completed_at = datetime.now(timezone.utc)
        await self._run_derived_step(
            "history-update",
            lambda: publish_history_update(
                execution_id=execution_id,
                status=status.value,
                executed_by=user_id,
                executed_by_name=user_name,
                workflow_name=workflow_name,
                org_id=org_id,
                completed_at=completed_at,
                duration_ms=duration_ms,
            ),
        )

        # Redis cleanup — no DB connection held
        try:
            from src.core.cache import cleanup_execution_cache
            await cleanup_execution_cache(execution_id)
        except Exception as e:
            logger.warning(f"Failed to cleanup cache for {execution_id[:8]}...: {e}")

        if not is_sync or sync_result_delivered:
            await self._run_derived_step(
                "pending-cleanup",
                lambda: self._redis_client.delete_pending_execution(execution_id),
            )
        else:
            logger.error(
                "Sync result delivery exhausted for %s; retaining pending context",
                execution_id,
            )

        logger.warning(
            f"Execution failed: {execution_id[:8]}... status={status.value} error={error_type}",
            extra={
                "execution_id": execution_id,
                "workflow_id": workflow_id,
                "status": status.value,
                "error_type": error_type,
                "duration_ms": duration_ms,
                "execution_model": "process",
            },
        )

        from src.services.events.builtins import emit_workflow_failure_events

        await self._run_derived_step(
            "workflow-failure-events",
            lambda: emit_workflow_failure_events(
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                execution_id=execution_id,
                organization_id=org_id,
                user_id=user_id,
                user_email=user_email,
                user_name=user_name,
                error_type=error_type,
                error_message=error,
                status=status.value,
                trigger_event=pending.get("event"),
            ),
        )

    async def _terminalize_result_without_pending(
        self, result: dict[str, Any]
    ) -> None:
        """Fail closed when ephemeral callback context disappeared.

        PostgreSQL attempt evidence remains authoritative even when Redis
        expires. We cannot safely flush a result without its scoped context, so
        record a bounded result-persistence failure instead of leaving the
        logical execution Running forever.
        """

        attempt_token = result.get("attempt_token")
        execution_id = result.get("execution_id")
        if not attempt_token or not execution_id:
            return
        from src.models.orm.executions import Execution
        from src.services.execution.attempts import finalize_attempt

        async with get_db_context() as db:
            await self._lock_execution(db, str(execution_id))
            execution = await db.scalar(
                select(Execution)
                .where(Execution.id == UUID(str(execution_id)))
                .with_for_update()
            )
            cancelling = (
                execution is not None
                and execution.status in {
                    ExecutionStatus.CANCELLED,
                    ExecutionStatus.CANCELLING,
                }
            )
            accepted = await finalize_attempt(
                db,
                UUID(execution_id),
                UUID(str(attempt_token)),
                status="cancelled" if cancelling else "failed",
                phase="result",
                failure_code=(
                    "cancelled" if cancelling else "result_context_missing"
                ),
                failure_phase="cancellation" if cancelling else "result",
                duration_ms=result.get("duration_ms"),
            )
            if not accepted:
                return
            if execution is not None and execution.status not in {
                ExecutionStatus.CANCELLED,
                ExecutionStatus.CANCELLING,
            }:
                execution.status = ExecutionStatus.FAILED
                execution.error_message = (
                    "Execution result could not be persisted because its "
                    "ephemeral context was unavailable"
                )
                execution.completed_at = datetime.now(timezone.utc)
            await db.commit()

    async def process_message(self, message_data: dict[str, Any]) -> None:
        """Process a workflow execution message."""
        from src.services.execution.queue_tracker import remove_from_queue

        dispatch_started = time.perf_counter()
        execution_id = message_data.get("execution_id", "")
        workflow_id = message_data.get("workflow_id")
        code_base64 = message_data.get("code")
        script_name = message_data.get("script_name")
        is_sync = message_data.get("sync", False)
        execution_record_exists = bool(
            message_data.get("execution_record_exists", False)
        )
        file_path: str | None = None  # Will be set from workflow metadata lookup
        start_time = datetime.now(timezone.utc)

        if not execution_id:
            raise MalformedMessage("workflow execution message missing execution_id")

        try:
            # Remove from queue tracking (execution is now being processed),
            # then load the context required before workflow code can run.
            await remove_from_queue(execution_id)
            pending = await self._redis_client.get_pending_execution(execution_id)
        except RedisError as exc:
            raise RetryableConsumerError(
                f"Redis pending execution state is unavailable: {exc}"
            ) from exc

        if pending is None:
            existing_status = await self._fail_missing_pending_execution(execution_id)
            if existing_status is not None:
                logger.info(
                    f"No pending execution found in Redis for {execution_id}, "
                    f"but durable execution already exists with status {existing_status}"
                )
                raise DuplicateMessage(
                    f"workflow execution {execution_id} already exists with status {existing_status}"
                )

            logger.error(f"No pending execution found in Redis: {execution_id}")
            raise RetryableConsumerError(
                f"pending execution not found in Redis: {execution_id}"
            )

        attempt_token = await self._claim_durable_execution(execution_id)
        if attempt_token is None:
            raise DuplicateMessage(
                f"workflow execution {execution_id} is not claimable"
            )
        pending_ready_ms = (time.perf_counter() - dispatch_started) * 1000

        # Extract context from Redis pending record
        parameters = pending["parameters"]
        org_id = pending["org_id"]
        user_id = pending["user_id"]
        user_name = pending["user_name"]
        user_email = pending["user_email"]
        form_id = pending.get("form_id")
        api_key_id = pending.get("api_key_id")  # Workflow ID whose API key triggered this
        startup = pending.get("startup")  # Launch workflow results
        form_inputs = pending.get("form_inputs", {})
        embed = pending.get("embed", {})
        event_data = pending.get("event")  # EventContext dict if event-triggered
        artifact_workspace_id = pending.get("artifact_workspace_id")

        # Determine if this is a code or workflow execution
        is_script = bool(code_base64)
        workflow_name = script_name or "inline_script"

        try:
            logger.info(
                f"Processing {'code' if is_script else 'workflow'} execution",
                extra={
                    "execution_id": execution_id,
                    "workflow_id": workflow_id,
                    "script_name": script_name,
                    "org_id": org_id,
                    "execution_model": "process",
                },
            )

            # Check if execution was cancelled in Redis before we started
            if pending.get("cancelled", False):
                logger.info(f"Execution {execution_id} was cancelled before starting")
                if attempt_token:
                    from src.services.execution.attempts import finalize_attempt

                    async with get_db_context() as db:
                        await self._lock_execution(db, execution_id)
                        accepted = await finalize_attempt(
                            db,
                            UUID(execution_id),
                            UUID(attempt_token),
                            status="cancelled",
                            phase="terminal",
                            failure_code="cancelled_before_start",
                            failure_phase="cancellation",
                            duration_ms=0,
                        )
                        if not accepted:
                            await db.rollback()
                            return
                        await update_execution(
                            execution_id=execution_id,
                            status=ExecutionStatus.CANCELLED,
                            error_message=_CANCELLED_BEFORE_START_MESSAGE,
                            duration_ms=0,
                            session=db,
                        )
                        await db.commit()
                else:
                    await create_execution(
                        execution_id=execution_id,
                        workflow_name=script_name or "workflow",
                        parameters=parameters,
                        org_id=org_id,
                        user_id=user_id,
                        user_name=user_name,
                        form_id=form_id,
                        api_key_id=api_key_id,
                        status=ExecutionStatus.CANCELLED,
                        execution_model="process",
                        workflow_id=workflow_id,
                        check_existing=execution_record_exists,
                    )
                    await update_execution(
                        execution_id=execution_id,
                        status=ExecutionStatus.CANCELLED,
                        error_message=_CANCELLED_BEFORE_START_MESSAGE,
                        duration_ms=0,
                    )
                await publish_execution_update(execution_id, "Cancelled")
                await publish_history_update(
                    execution_id=execution_id,
                    status="Cancelled",
                    executed_by=user_id,
                    executed_by_name=user_name,
                    workflow_name=script_name or "workflow",
                    org_id=org_id,
                )
                await self._redis_client.delete_pending_execution(execution_id)
                if is_sync:
                    await self._redis_client.push_result(
                        execution_id=execution_id,
                        status="Cancelled",
                        error=_CANCELLED_BEFORE_START_MESSAGE,
                        duration_ms=0,
                    )
                return

            # Get workflow metadata from database if this is a workflow execution
            timeout_seconds = 1800  # Default 30 minutes
            roi_time_saved = 0
            roi_value = 0.0
            workflow_function_name: str | None = None  # Function name for exec_from_db()
            content_hash: str | None = None  # Content hash pinned at dispatch time
            workflow_type = "workflow"
            cache_ttl_seconds = 300
            solution_id: str | None = None  # Install id if solution-managed
            solution_global_repo_access = False  # Whether solution code may import _repo/
            solution_deployment_id = pending.get("solution_deployment_id")
            runtime_mode = pending.get("runtime_mode") or "legacy"
            runtime_storage_prefix: str | None = None
            workspace_release_id: str | None = None
            workspace_release_source_hashes: dict[str, str] | None = None
            runtime_max_duration_seconds: int | None = None
            runtime_max_output_bytes: int | None = None

            if not is_script and runtime_mode == "workspace-canary-v1":
                from src.models.orm.executions import Execution
                from src.services.workspace_draft_canary import (
                    WorkspaceDraftCanaryError,
                    resolve_draft_runtime_evidence,
                    workflow_data_from_draft_evidence,
                )

                try:
                    async with get_db_context() as db:
                        execution = await db.get(Execution, UUID(execution_id))
                        if (
                            execution is None
                            or execution.workflow_id is not None
                            or execution.runtime_mode != "workspace-canary-v1"
                        ):
                            raise WorkspaceDraftCanaryError(
                                "draft canary is missing its durable execution pin"
                            )
                        evidence = await resolve_draft_runtime_evidence(
                            db, pending.get("runtime_evidence"), execution
                        )
                    workflow_data = workflow_data_from_draft_evidence(evidence)
                except WorkspaceDraftCanaryError as exc:
                    raise RuntimeError(str(exc)) from exc
                workflow_name = workflow_data["name"]
                workflow_function_name = workflow_data["function_name"]
                file_path = workflow_data["path"]
                workflow_type = workflow_data["type"]
                cache_ttl_seconds = workflow_data["cache_ttl_seconds"]
                timeout_seconds = workflow_data["timeout_seconds"]
                content_hash = workflow_data["content_hash"]
                runtime_storage_prefix = workflow_data["runtime_storage_prefix"]
                workspace_release_source_hashes = workflow_data["source_hashes"]
                workspace_release_id = workflow_data["draft_runtime_id"]
                runtime_max_duration_seconds = timeout_seconds
                runtime_max_output_bytes = workflow_data["max_output_bytes"]
            elif not is_script and workflow_id:
                from src.services.execution.service import get_workflow_for_execution, WorkflowNotFoundError

                try:
                    # Get workflow metadata (no code — worker loads via Redis→S3)
                    # Brief DB session for metadata read
                    if runtime_mode == "workspace-release-v1":
                        from src.models.orm.executions import Execution
                        from src.services.workspace_release_runtime import (
                            WorkspaceReleaseRuntimeError,
                            resolve_pinned_workspace_runtime,
                            verify_workspace_runtime_evidence,
                            workflow_data_from_workspace_evidence,
                        )

                        try:
                            queued_evidence = pending.get("runtime_evidence")
                            release_id = (
                                queued_evidence.get("workspace_release_id")
                                if isinstance(queued_evidence, dict)
                                else None
                            )
                            if not isinstance(release_id, str) or not release_id:
                                raise WorkspaceReleaseRuntimeError(
                                    "queued execution is missing its Workspace release id"
                                )
                            async with get_db_context() as db:
                                execution = await db.get(Execution, UUID(execution_id))
                                if (
                                    execution is None
                                    or execution.runtime_mode != "workspace-release-v1"
                                ):
                                    raise WorkspaceReleaseRuntimeError(
                                        "Workspace release execution is missing durable pin evidence"
                                    )
                                pinned = await resolve_pinned_workspace_runtime(
                                    db, queued_evidence, UUID(str(workflow_id))
                                )
                            authoritative_evidence = pinned.queue_evidence()
                            verify_workspace_runtime_evidence(
                                queued_evidence,
                                execution.runtime_evidence,
                                execution.runtime_evidence_hash,
                                authoritative_evidence,
                            )
                            workflow_data = workflow_data_from_workspace_evidence(
                                authoritative_evidence
                            )
                        except WorkspaceReleaseRuntimeError as exc:
                            raise WorkflowNotFoundError(str(exc)) from exc
                        workspace_release_id = workflow_data["workspace_release_id"]
                        workspace_release_source_hashes = workflow_data[
                            "workspace_release_source_hashes"
                        ]
                        runtime_storage_prefix = workflow_data[
                            "workspace_release_runtime_storage_prefix"
                        ]
                        runtime_max_duration_seconds = workflow_data[
                            "workflow_runtime_bounds"
                        ]["max_duration_seconds"]
                        runtime_max_output_bytes = workflow_data[
                            "workspace_release_max_output_bytes"
                        ]
                    elif solution_deployment_id:
                        from src.services.solutions.deployment_runtime import (
                            DeploymentRuntimeError,
                            resolve_pinned_workflow_runtime,
                            verify_runtime_evidence,
                            workflow_data_from_evidence,
                        )
                        from src.models.orm.executions import Execution

                        try:
                            async with get_db_context() as db:
                                execution = await db.get(Execution, UUID(execution_id))
                                if execution is None or execution.runtime_mode != "deployment-v1":
                                    raise DeploymentRuntimeError(
                                        "modern deployment execution is missing durable pin evidence"
                                    )
                                if str(execution.solution_deployment_id) != str(solution_deployment_id):
                                    raise DeploymentRuntimeError("durable deployment pin mismatch")
                                queued_evidence = pending.get("runtime_evidence")
                                pinned = await resolve_pinned_workflow_runtime(
                                    db, UUID(str(solution_deployment_id)), UUID(str(workflow_id))
                                )
                            authoritative_evidence = pinned.queue_evidence()
                            verify_runtime_evidence(
                                str(solution_deployment_id),
                                queued_evidence,
                                execution.runtime_evidence,
                                execution.runtime_evidence_hash,
                                authoritative_evidence,
                            )
                            workflow_data = workflow_data_from_evidence(
                                str(solution_deployment_id), authoritative_evidence
                            )
                        except DeploymentRuntimeError as exc:
                            raise WorkflowNotFoundError(str(exc)) from exc
                        runtime_storage_prefix = workflow_data["runtime_storage_prefix"]
                    else:
                        # Compatibility boundary: newly enqueued Solution work is
                        # always pinned above, so a null deployment here is either
                        # `_repo` or a pre-migration pending/scheduled execution.
                        if runtime_mode not in {"legacy", "repo-v1"}:
                            raise WorkflowNotFoundError(
                                "modern Solution execution is missing deployment pin"
                            )
                        async with get_db_context() as db:
                            workflow_data = await get_workflow_for_execution(workflow_id, db=db)
                    workflow_name = workflow_data["name"]
                    workflow_function_name = workflow_data["function_name"]
                    file_path = workflow_data["path"]  # Used for __file__ injection and Redis/S3 loading
                    workflow_type = workflow_data["type"]
                    cache_ttl_seconds = workflow_data["cache_ttl_seconds"]

                    timeout_seconds = workflow_data["timeout_seconds"]
                    # Initialize ROI from workflow defaults
                    roi_time_saved = workflow_data["time_saved"]
                    roi_value = workflow_data["value"]
                    content_hash = workflow_data.get("content_hash")

                    # Solution scoping: if the workflow is solution-managed, its
                    # code + imports must resolve under _solutions/{id}/ (with
                    # _repo/ fallback only when the install allows it). Look up
                    # the install's global_repo_access here so the worker can set
                    # the per-execution import root. See module_cache_sync.
                    solution_id = workflow_data.get("solution_id")
                    # global_repo_access now rides on workflow_data from the same
                    # DB grab as the metadata (get_workflow_for_execution). The
                    # engine subprocess has no DB; this is the last enrichment.
                    solution_global_repo_access = workflow_data.get(
                        "can_access_global_repo", False
                    )

                    # Scope resolution: org-scoped workflows use workflow's org,
                    # global workflows use caller's org
                    workflow_org_id = workflow_data.get("organization_id")
                    if workflow_org_id:
                        # Org-scoped workflow: always use workflow's org
                        org_id = workflow_org_id
                        logger.info(f"Scope: workflow org {org_id} (org-scoped workflow)")
                    else:
                        # Global workflow: use caller's org (already set from pending["org_id"])
                        logger.info(f"Scope: caller org {org_id or 'GLOBAL'} (global workflow)")
                except WorkflowNotFoundError:
                    logger.error(f"Workflow not found: {workflow_id}")
                    raise
            metadata_ready_ms = (time.perf_counter() - dispatch_started) * 1000

            # Store additional context in pending record for result handler
            # (needed when pool reports results asynchronously)
            await self._redis_client.update_pending_execution(
                execution_id=execution_id,
                updates={
                    "workflow_name": workflow_name,
                    "workflow_id": workflow_id,
                    "org_id": org_id,  # Resolved scope for result handlers
                },
            )

            # Create PostgreSQL record with RUNNING status
            await create_execution(
                execution_id=execution_id,
                workflow_name=workflow_name,
                parameters=parameters,
                org_id=org_id,
                user_id=user_id,
                user_name=user_name,
                form_id=form_id,
                api_key_id=api_key_id,
                status=ExecutionStatus.RUNNING,
                execution_model="process",
                workflow_id=workflow_id,
                solution_deployment_id=solution_deployment_id,
            )
            execution_created_ms = (time.perf_counter() - dispatch_started) * 1000
            if not is_sync:
                await publish_execution_update(execution_id, "Running")
                await publish_history_update(
                    execution_id=execution_id,
                    status="Running",
                    executed_by=user_id,
                    executed_by_name=user_name,
                    workflow_name=workflow_name,
                    org_id=org_id,
                    started_at=start_time,
                )
            running_published_ms = (time.perf_counter() - dispatch_started) * 1000

            # Rehydrate the org from org_id (the enqueue boundary only carried
            # the scalar org_id, not the Organization object built API-side).
            # is_provider MUST come through here — it is the SDK-side C2
            # scope-bypass flag the worker hands to resolve_scope. See
            # OrganizationRepository.get_with_cache.
            org = None
            org_data = None

            if org_id:
                from src.repositories.organizations import OrganizationRepository

                async with get_db_context() as db:
                    org = await OrganizationRepository(db).get_with_cache(org_id)
                if org:
                    org_data = {
                        "id": org.id,
                        "name": org.name,
                        "is_active": org.is_active,
                        "is_provider": org.is_provider,
                    }

            # Mint engine token parent-side (consumer holds SECRET_KEY legitimately).
            # The child receives it through context_data and installs it only in
            # its one-shot process environment — no SECRET_KEY or persistent
            # credential write is needed in the child.
            from src.core.security import mint_engine_token
            engine_token, engine_token_expires_at = mint_engine_token(
                execution_id=execution_id,
                solution_id=solution_id,
                global_repo_access=solution_global_repo_access,
                timeout_seconds=timeout_seconds,
                organization_id=org_id,
                delegated_user_id=user_id,
                delegated_email=user_email,
                delegated_name=user_name,
                delegated_is_superuser=pending.get("is_platform_admin", False),
                delegated_is_provider_org=pending.get("is_provider_org", False),
                delegated_is_external=pending.get("is_external", False),
            )

            # Build context for worker process
            context_data = {
                "execution_id": execution_id,
                "workflow_id": workflow_id,
                "name": workflow_name,
                "function_name": workflow_function_name,  # For exec_from_db()
                "code": code_base64,  # Base64-encoded inline script (different from workflow_code)
                "parameters": parameters,
                "caller": {
                    "user_id": user_id,
                    "email": user_email,
                    "name": user_name,
                },
                "organization": org_data,
                "tags": [workflow_type] if not is_script else [],
                "timeout_seconds": timeout_seconds,
                "cache_ttl_seconds": cache_ttl_seconds,
                "transient": False,
                "is_platform_admin": pending.get("is_platform_admin", False),
                "is_provider_org": pending.get("is_provider_org", False),
                "is_external": pending.get("is_external", False),
                "startup": startup,  # Launch workflow results (available via context.startup)
                "form_inputs": form_inputs,
                "embed": embed,
                "roi": {
                    "time_saved": roi_time_saved,
                    "value": roi_value,
                },
                "file_path": file_path,  # Path for __file__ injection and fallback loading
                "content_hash": content_hash,  # Pinned hash at dispatch time
                "event": event_data,  # EventContext dict (None if not event-triggered)
                "solution_id": solution_id,  # Install id if solution-managed (else None)
                "solution_deployment_id": solution_deployment_id,
                "runtime_storage_prefix": runtime_storage_prefix,
                "deployment_source_hashes": (
                    pending.get("runtime_evidence") or {}
                ).get("deployment_source_hashes"),
                "workspace_release_id": workspace_release_id,
                "workspace_release_runtime_storage_prefix": (
                    runtime_storage_prefix if workspace_release_id else None
                ),
                "workspace_release_source_hashes": workspace_release_source_hashes,
                "workspace_generation": workspace_release_id,
                "runtime_mode": runtime_mode,
                "runtime_max_duration_seconds": runtime_max_duration_seconds,
                "runtime_max_output_bytes": runtime_max_output_bytes,
                "solution_global_repo_access": solution_global_repo_access,
                "artifact_workspace_id": artifact_workspace_id,
                # Pre-minted engine token: child uses process-scoped SDK
                # credentials, with no SECRET_KEY in its environment.
                "engine_token": engine_token,
            }

            # Route to process pool
            # Results are handled asynchronously via _handle_result callback
            await self._pool.route_execution(
                execution_id=execution_id,
                context=context_data,
                attempt_token=attempt_token or None,
            )
            logger.debug(
                "Dispatch timing %s: pending=%.1fms metadata=%.1fms "
                "execution_row=%.1fms running_events=%.1fms routed=%.1fms",
                execution_id[:8],
                pending_ready_ms,
                metadata_ready_ms,
                execution_created_ms,
                running_published_ms,
                (time.perf_counter() - dispatch_started) * 1000,
            )
            # Don't wait for result - pool will call back

        except asyncio.CancelledError:
            logger.info(f"Execution task {execution_id} was cancelled")
            if attempt_token:
                await self._release_durable_execution_claim(
                    execution_id,
                    attempt_token=attempt_token,
                    attempt_status="failed",
                    failure_code="consumer_cancelled_before_route",
                    failure_phase="claim",
                )
            raise

        except (MemoryError, ProcessPoolAdmissionRejected) as e:
            # Admission rejected due to memory pressure — requeue for retry
            logger.warning(
                f"Admission rejected for {execution_id[:8]}: {e}. "
                "Will requeue for retry."
            )
            # Don't mark as failed — the execution hasn't started yet.
            # Keep pending state intact so the requeued message can be routed later.
            if attempt_token:
                await self._release_durable_execution_claim(
                    execution_id, attempt_token=attempt_token
                )
            else:
                await self._release_durable_execution_claim(execution_id)
            # Re-raise so the consumer framework NACKs with requeue=True
            raise RetryableConsumerError(f"process pool admission rejected: {e}") from e

        except Exception as e:
            # Unexpected error during setup (before routing to pool)
            duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
            completed_at = datetime.now(timezone.utc)
            error_msg = str(e)
            error_type = type(e).__name__

            if attempt_token:
                from src.core.database import get_session_factory
                from src.models.orm.executions import Execution
                from src.services.execution.attempts import finalize_attempt

                session_factory = get_session_factory()
                async with session_factory() as session:
                    await self._lock_execution(session, execution_id)
                    execution_row = await session.scalar(
                        select(Execution)
                        .where(Execution.id == UUID(execution_id))
                        .with_for_update()
                    )
                    cancelling = (
                        execution_row is not None
                        and execution_row.status == ExecutionStatus.CANCELLING
                    )
                    accepted = await finalize_attempt(
                        session,
                        UUID(execution_id),
                        UUID(attempt_token),
                        status="cancelled" if cancelling else "failed",
                        phase="terminal",
                        failure_code=(
                            "cancelled"
                            if cancelling
                            else (
                                "workflow_not_found"
                                if error_type == "WorkflowNotFoundError"
                                else "setup_failed"
                            )
                        ),
                        failure_phase="cancellation" if cancelling else "claim",
                        duration_ms=duration_ms,
                    )
                    if accepted:
                        await update_execution(
                            execution_id=execution_id,
                            status=(
                                ExecutionStatus.CANCELLED
                                if cancelling
                                else ExecutionStatus.FAILED
                            ),
                            error_message=error_msg,
                            error_type=error_type,
                            duration_ms=duration_ms,
                            session=session,
                        )
                        await session.commit()
                    else:
                        logger.warning(
                            "Rejected stale workflow setup failure for %s",
                            execution_id,
                        )
                        return
            else:
                await update_execution(
                    execution_id=execution_id,
                    status=ExecutionStatus.FAILED,
                    error_message=error_msg,
                    error_type=error_type,
                    duration_ms=duration_ms,
                )

            await publish_execution_update(
                execution_id,
                "Failed",
                {"error": error_msg, "errorType": error_type},
            )
            await publish_history_update(
                execution_id=execution_id,
                status="Failed",
                executed_by=user_id,
                executed_by_name=user_name,
                workflow_name=workflow_name,
                org_id=org_id,
                started_at=start_time,
                completed_at=completed_at,
                duration_ms=duration_ms,
            )

            await self._redis_client.delete_pending_execution(execution_id)

            if is_sync:
                await self._redis_client.push_result(
                    execution_id=execution_id,
                    status="Failed",
                    error=error_msg,
                    error_type=error_type,
                    duration_ms=duration_ms,
                )

            logger.error(
                f"Workflow execution error: {execution_id}",
                extra={
                    "execution_id": execution_id,
                    "workflow_id": workflow_id,
                    "error": error_msg,
                    "error_type": error_type,
                    "execution_model": "process",
                },
                exc_info=True,
            )
            raise DomainFailureHandled("workflow setup failure recorded") from e

    async def _finalize_poison_delivery(
        self,
        context: DeliveryContext,
        *,
        reason: str,
    ) -> None:
        """Terminalize a poisoned execution before RabbitMQ acknowledges it."""
        from src.services.execution.poison import (
            PoisonFinalizationConflict,
            finalize_poisoned_execution,
        )

        execution_id = context.body.get("execution_id")
        try:
            UUID(str(execution_id))
        except (TypeError, ValueError):
            # Malformed messages have no durable execution to reconcile. The
            # poison record is their terminal audit evidence; requeueing after
            # it was published would manufacture duplicate poison messages.
            logger.warning(
                "Poisoned workflow delivery has no valid execution UUID; "
                "skipping durable execution finalization",
                extra={
                    "queue": context.queue_name,
                    "message_id": context.message_id,
                    "reason": reason,
                },
            )
            return
        try:
            await finalize_poisoned_execution(
                execution_id=str(execution_id),
                queue=context.queue_name,
                reason=reason,
                retry_count=context.retry_count,
                replay_count=context.replay_count,
                message_id=context.message_id,
                sync=bool(context.body.get("sync")),
            )
        except PoisonFinalizationConflict as exc:
            raise MalformedMessage(str(exc)) from exc

    async def _fail_missing_pending_execution(
        self,
        execution_id: str,
    ) -> str | None:
        """Fail confirmed work whose required Redis context disappeared.

        Taking the publisher's advisory transaction lock prevents observing
        SCHEDULED while a confirmed publication is still advancing to PENDING.
        Unconfirmed SCHEDULED and terminal rows remain untouched.
        """
        from src.core.database import get_db_context
        from src.models.enums import ExecutionStatus
        from src.models.orm.executions import Execution, WorkflowExecutionAttempt

        ExecutionAttempt = WorkflowExecutionAttempt

        try:
            execution_uuid = UUID(execution_id)
        except ValueError as exc:
            raise MalformedMessage(
                f"invalid execution_id UUID: {execution_id}"
            ) from exc

        async with get_db_context() as db:
            await self._lock_execution(db, execution_id)
            execution = await db.get(Execution, execution_uuid)
            if execution is None:
                return None
            if execution.status == ExecutionStatus.PENDING:
                attempt = await db.scalar(
                    select(ExecutionAttempt)
                    .where(
                        ExecutionAttempt.execution_id == execution_uuid,
                        ExecutionAttempt.completed_at.is_(None),
                    )
                    .with_for_update()
                )
                completed_at = datetime.now(timezone.utc)
                if attempt is not None:
                    attempt.status = "failed"
                    attempt.phase = "terminal"
                    attempt.failure_phase = "queue"
                    attempt.failure_code = "pending_context_missing"
                    attempt.completed_at = completed_at
                    attempt.heartbeat_at = completed_at
                execution.status = ExecutionStatus.FAILED
                execution.error_message = (
                    "Execution context was unavailable before execution"
                )
                execution.completed_at = completed_at
                await db.commit()
            return execution.status.value

    async def _claim_durable_execution(self, execution_id: str) -> str | None:
        """Claim a published durable execution before any execution side effects.

        The publisher holds the same advisory transaction lock through broker
        confirmation and its SCHEDULED -> PENDING transition. A fast consumer
        therefore waits for that transaction, then advances only PENDING to
        RUNNING. SCHEDULED means publication was not durably confirmed; any
        other state is a redelivery or an already-finished execution.

        Rows are absent for legacy inline-code dispatch, which retains its
        existing Redis-first creation path.
        """
        from src.core.database import get_db_context
        from src.models.enums import ExecutionStatus
        from src.models.orm.executions import Execution

        try:
            execution_uuid = UUID(execution_id)
        except ValueError as exc:
            raise MalformedMessage(
                f"invalid execution_id UUID: {execution_id}"
            ) from exc

        async with get_db_context() as db:
            await self._lock_execution(db, execution_id)
            execution = await db.get(Execution, execution_uuid)
            if execution is None:
                return ""
            if execution.status != ExecutionStatus.PENDING:
                return None
            from src.services.execution.attempts import create_claimed_attempt

            claim = await create_claimed_attempt(
                db,
                execution,
                worker_id=self._pool.worker_id,
                worker_incarnation_id=self._pool.worker_incarnation_id,
            )
            execution.status = ExecutionStatus.RUNNING
            await db.commit()
            return str(claim.claim_token)

    async def _release_durable_execution_claim(
        self,
        execution_id: str,
        *,
        attempt_token: str | None = None,
        attempt_status: str = "admission_rejected",
        failure_code: str = "admission_rejected",
        failure_phase: str = "admission",
    ) -> None:
        """Make an admission-rejected claim retryable without undoing cancellation."""
        from src.core.database import get_db_context
        from src.models.enums import ExecutionStatus
        from src.models.orm.executions import Execution

        async with get_db_context() as db:
            await self._lock_execution(db, execution_id)
            execution = await db.get(Execution, UUID(execution_id))
            if execution is not None and execution.status in {
                ExecutionStatus.RUNNING,
                ExecutionStatus.CANCELLING,
            }:
                if attempt_token:
                    from src.services.execution.attempts import finalize_attempt

                    cancellation_won = (
                        execution.status == ExecutionStatus.CANCELLING
                    )
                    accepted = await finalize_attempt(
                        db,
                        UUID(execution_id),
                        UUID(attempt_token),
                        status="cancelled" if cancellation_won else attempt_status,
                        phase="terminal",
                        failure_code=(
                            "cancelled_during_admission"
                            if cancellation_won
                            else failure_code
                        ),
                        failure_phase=(
                            "cancellation" if cancellation_won else failure_phase
                        ),
                    )
                    if not accepted:
                        return
                execution.status = (
                    ExecutionStatus.CANCELLED
                    if execution.status == ExecutionStatus.CANCELLING
                    else ExecutionStatus.PENDING
                )
                if execution.status == ExecutionStatus.CANCELLED:
                    execution.completed_at = datetime.now(timezone.utc)
                await db.commit()
