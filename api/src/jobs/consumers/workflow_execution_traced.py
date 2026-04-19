"""Traced workflow execution consumer.

Adds OpenTelemetry spans around the existing workflow execution consumer
without changing core execution logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
from src.observability.otel import get_tracer

tracer = get_tracer(__name__)


class TracedWorkflowExecutionConsumer(WorkflowExecutionConsumer):
    """Workflow execution consumer with execution-level tracing."""

    async def process_message(self, message_data: dict[str, Any]) -> None:
        execution_id = message_data.get("execution_id", "")
        workflow_id = message_data.get("workflow_id")
        is_sync = bool(message_data.get("sync", False))
        is_script = bool(message_data.get("code"))

        pending = await self._redis_client.get_pending_execution(execution_id)

        with tracer.start_as_current_span("workflow.execution") as span:
            span.set_attribute("execution.id", execution_id)
            if workflow_id:
                span.set_attribute("workflow.id", workflow_id)
            span.set_attribute("execution.sync", is_sync)
            span.set_attribute("execution.is_script", is_script)

            if pending:
                org_id = pending.get("org_id")
                if org_id:
                    span.set_attribute("organization.id", org_id)
                created_at_raw = pending.get("created_at")
                if created_at_raw:
                    try:
                        created_at = datetime.fromisoformat(created_at_raw)
                        now = datetime.now(timezone.utc)
                        queue_latency_ms = max(0, int((now - created_at).total_seconds() * 1000))
                        span.set_attribute("queue.latency_ms", queue_latency_ms)
                    except ValueError:
                        pass

            original_route_execution: Callable[..., Awaitable[None]] = self._pool.route_execution

            async def traced_route_execution(*args: Any, **kwargs: Any) -> None:
                routed_execution_id = kwargs.get("execution_id") or (args[0] if args else execution_id)
                routed_workflow_id = None
                context = kwargs.get("context") or (args[1] if len(args) > 1 else None)
                if isinstance(context, dict):
                    routed_workflow_id = context.get("workflow_id")
                with tracer.start_as_current_span("workflow.execute.process_pool") as pool_span:
                    pool_span.set_attribute("execution.id", routed_execution_id)
                    if routed_workflow_id:
                        pool_span.set_attribute("workflow.id", routed_workflow_id)
                    await original_route_execution(*args, **kwargs)

            self._pool.route_execution = traced_route_execution
            try:
                await super().process_message(message_data)
            except Exception as exc:
                span.set_attribute("error", True)
                span.set_attribute("execution.error_type", type(exc).__name__)
                raise
            finally:
                self._pool.route_execution = original_route_execution

    async def _process_success(self, execution_id: str, result: dict[str, Any]) -> None:
        pending = await self._redis_client.get_pending_execution(execution_id)
        with tracer.start_as_current_span("workflow.persist.success") as span:
            span.set_attribute("execution.id", execution_id)
            span.set_attribute("execution.duration_ms", int(result.get("duration_ms", 0) or 0))
            if pending:
                workflow_id = pending.get("workflow_id")
                org_id = pending.get("org_id")
                if workflow_id:
                    span.set_attribute("workflow.id", workflow_id)
                if org_id:
                    span.set_attribute("organization.id", org_id)
            await super()._process_success(execution_id, result)

    async def _process_failure(self, execution_id: str, result: dict[str, Any]) -> None:
        pending = await self._redis_client.get_pending_execution(execution_id)
        with tracer.start_as_current_span("workflow.persist.failure") as span:
            span.set_attribute("execution.id", execution_id)
            span.set_attribute("execution.duration_ms", int(result.get("duration_ms", 0) or 0))
            span.set_attribute("execution.error_type", result.get("error_type", "ExecutionError"))
            span.set_attribute("error", True)
            if pending:
                workflow_id = pending.get("workflow_id")
                org_id = pending.get("org_id")
                if workflow_id:
                    span.set_attribute("workflow.id", workflow_id)
                if org_id:
                    span.set_attribute("organization.id", org_id)
            await super()._process_failure(execution_id, result)
