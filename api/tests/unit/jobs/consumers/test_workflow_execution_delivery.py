"""Delivery outcome tests for the workflow execution consumer."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

sys.modules.setdefault(
    "resource",
    SimpleNamespace(
        RUSAGE_SELF=0,
        getrusage=lambda _who: SimpleNamespace(ru_maxrss=0),
    ),
)

from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer, workflow_prefetch_count  # noqa: E402
from src.jobs.rabbitmq import (  # noqa: E402
    DomainFailureHandled,
    DuplicateMessage,
    MalformedMessage,
    RetryableConsumerError,
)
from src.services.execution.process_pool import ProcessPoolAdmissionRejected  # noqa: E402


def make_consumer() -> WorkflowExecutionConsumer:
    """Create a consumer without wiring real Redis, RabbitMQ, or process pool clients."""
    with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
        consumer = WorkflowExecutionConsumer()

    consumer._redis_client = AsyncMock()
    return consumer


def pending_context() -> dict[str, object]:
    return {
        "parameters": {},
        "org_id": None,
        "user_id": str(uuid4()),
        "user_name": "Test User",
        "user_email": "test@example.com",
    }


class _Session:
    def __init__(self) -> None:
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _session_factory(session: _Session):
    return lambda: session


def test_workflow_prefetch_count_is_capped_by_process_capacity() -> None:
    """Workflow consumer prefetch should not exceed local process slots."""
    settings = type(
        "Settings",
        (),
        {
            "max_concurrency": 20,
            "max_workers": 5,
        },
    )()

    assert workflow_prefetch_count(settings) == 5


def test_workflow_prefetch_count_keeps_lower_global_concurrency() -> None:
    """A lower global concurrency limit should still constrain prefetch."""
    settings = type(
        "Settings",
        (),
        {
            "max_concurrency": 3,
            "max_workers": 10,
        },
    )()

    assert workflow_prefetch_count(settings) == 3


def test_workflow_prefetch_count_has_minimum_of_one() -> None:
    """Workflow consumer prefetch must never be configured below one."""
    settings = type(
        "Settings",
        (),
        {
            "max_concurrency": 0,
            "max_workers": 0,
        },
    )()

    assert workflow_prefetch_count(settings) == 1


@pytest.mark.asyncio
async def test_process_message_rejects_missing_execution_id() -> None:
    consumer = make_consumer()
    consumer._redis_client.get_pending_execution.return_value = None

    with patch(
        "src.services.execution.queue_tracker.remove_from_queue",
        new_callable=AsyncMock,
    ) as remove_from_queue:
        with pytest.raises(MalformedMessage, match="execution_id"):
            await consumer.process_message({})

    remove_from_queue.assert_not_called()
    consumer._redis_client.get_pending_execution.assert_not_called()


@pytest.mark.asyncio
async def test_process_message_treats_existing_execution_without_pending_context_as_duplicate() -> None:
    consumer = make_consumer()
    execution_id = str(uuid4())
    consumer._redis_client.get_pending_execution.return_value = None
    consumer._get_existing_execution_status = AsyncMock(return_value="Success")  # type: ignore[attr-defined]

    with patch(
        "src.services.execution.queue_tracker.remove_from_queue",
        new_callable=AsyncMock,
    ) as remove_from_queue:
        with pytest.raises(DuplicateMessage, match="already exists"):
            await consumer.process_message({"execution_id": execution_id})

    remove_from_queue.assert_awaited_once_with(execution_id)
    consumer._get_existing_execution_status.assert_awaited_once_with(execution_id)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_process_message_retries_missing_pending_context() -> None:
    consumer = make_consumer()
    execution_id = str(uuid4())
    consumer._redis_client.get_pending_execution.return_value = None
    consumer._redis_client.push_result = AsyncMock()
    consumer._get_existing_execution_status = AsyncMock(return_value=None)  # type: ignore[attr-defined]

    with patch(
        "src.services.execution.queue_tracker.remove_from_queue",
        new_callable=AsyncMock,
    ):
        with pytest.raises(RetryableConsumerError, match="pending execution"):
            await consumer.process_message({"execution_id": execution_id, "sync": True})

    consumer._redis_client.push_result.assert_not_called()


@pytest.mark.asyncio
async def test_handle_result_dispatches_success_and_failure() -> None:
    consumer = make_consumer()
    consumer._process_success = AsyncMock()  # type: ignore[method-assign]
    consumer._process_failure = AsyncMock()  # type: ignore[method-assign]

    await consumer._handle_result({"execution_id": "exec-success", "success": True})
    await consumer._handle_result({"execution_id": "exec-failure", "success": False})

    consumer._process_success.assert_awaited_once_with(
        "exec-success",
        {"execution_id": "exec-success", "success": True},
    )
    consumer._process_failure.assert_awaited_once_with(
        "exec-failure",
        {"execution_id": "exec-failure", "success": False},
    )


@pytest.mark.asyncio
async def test_process_success_and_failure_ignore_results_without_pending_context() -> None:
    consumer = make_consumer()
    consumer._redis_client.get_pending_execution.return_value = None
    consumer._redis_client.delete_pending_execution = AsyncMock()
    consumer._redis_client.push_result = AsyncMock()

    await consumer._process_success(
        "missing-success",
        {"success": True, "result": {"ok": True}, "duration_ms": 12},
    )
    await consumer._process_failure(
        "missing-failure",
        {"success": False, "error": "boom", "error_type": "ExecutionError"},
    )

    assert consumer._redis_client.get_pending_execution.await_count == 2
    consumer._redis_client.delete_pending_execution.assert_not_called()
    consumer._redis_client.push_result.assert_not_called()


@pytest.mark.asyncio
async def test_process_message_records_cancelled_before_start_for_sync_execution() -> None:
    consumer = make_consumer()
    execution_id = str(uuid4())
    pending = pending_context()
    pending["cancelled"] = True
    pending["sync"] = True
    consumer._redis_client.get_pending_execution.return_value = pending
    consumer._redis_client.delete_pending_execution = AsyncMock()
    consumer._redis_client.push_result = AsyncMock()

    with (
        patch(
            "src.services.execution.queue_tracker.remove_from_queue",
            new_callable=AsyncMock,
        ) as remove_from_queue,
        patch("src.repositories.executions.create_execution", new_callable=AsyncMock) as create_execution,
        patch("src.repositories.executions.update_execution", new_callable=AsyncMock) as update_execution,
        patch(
            "src.jobs.consumers.workflow_execution.publish_execution_update",
            new_callable=AsyncMock,
        ) as publish_execution_update,
        patch(
            "src.jobs.consumers.workflow_execution.publish_history_update",
            new_callable=AsyncMock,
        ) as publish_history_update,
    ):
        await consumer.process_message(
            {
                "execution_id": execution_id,
                "code": "cHJpbnQoJ2hpJyk=",
                "script_name": "inline.py",
                "sync": True,
            }
        )

    remove_from_queue.assert_awaited_once_with(execution_id)
    create_execution.assert_awaited_once()
    update_execution.assert_awaited_once()
    assert create_execution.await_args.kwargs["status"].value == "Cancelled"
    assert update_execution.await_args.kwargs["error_message"] == (
        "Execution was cancelled before it could start"
    )
    publish_execution_update.assert_awaited_once_with(execution_id, "Cancelled")
    publish_history_update.assert_awaited_once()
    consumer._redis_client.delete_pending_execution.assert_awaited_once_with(execution_id)
    consumer._redis_client.push_result.assert_awaited_once_with(
        execution_id=execution_id,
        status="Cancelled",
        error="Execution was cancelled before it could start",
        duration_ms=0,
    )


@pytest.mark.asyncio
async def test_process_message_retries_pool_admission_memory_pressure_without_deleting_pending() -> None:
    consumer = make_consumer()
    execution_id = str(uuid4())
    consumer._pool = AsyncMock()
    consumer._pool.route_execution = AsyncMock(
        side_effect=ProcessPoolAdmissionRejected("limit reached")
    )
    consumer._redis_client.get_pending_execution.return_value = pending_context()
    consumer._redis_client.delete_pending_execution = AsyncMock()

    with (
        patch(
            "src.services.execution.queue_tracker.remove_from_queue",
            new_callable=AsyncMock,
        ),
        patch("src.repositories.executions.create_execution", new_callable=AsyncMock) as create_execution,
        patch("src.repositories.executions.update_execution", new_callable=AsyncMock) as update_execution,
        patch(
            "src.jobs.consumers.workflow_execution.publish_execution_update",
            new_callable=AsyncMock,
        ) as publish_execution_update,
        patch(
            "src.jobs.consumers.workflow_execution.publish_history_update",
            new_callable=AsyncMock,
        ),
    ):
        with pytest.raises(RetryableConsumerError, match="admission"):
            await consumer.process_message(
                {
                    "execution_id": execution_id,
                    "code": "cHJpbnQoJ2hpJyk=",
                    "script_name": "inline.py",
                }
            )

    consumer._redis_client.delete_pending_execution.assert_not_called()
    create_execution.assert_awaited_once()
    update_execution.assert_not_awaited()
    consumer._pool.route_execution.assert_awaited_once()
    publish_execution_update.assert_awaited_once()
    assert publish_execution_update.await_args is not None
    assert publish_execution_update.await_args.args[:2] == (execution_id, "Running")


@pytest.mark.asyncio
async def test_process_message_acknowledges_recorded_setup_failure_as_domain_handled() -> None:
    consumer = make_consumer()
    execution_id = str(uuid4())
    consumer._pool = AsyncMock()
    consumer._pool.route_execution = AsyncMock(side_effect=ValueError("bad setup"))
    consumer._redis_client.get_pending_execution.return_value = pending_context()
    consumer._redis_client.delete_pending_execution = AsyncMock()
    consumer._redis_client.push_result = AsyncMock()

    with (
        patch(
            "src.services.execution.queue_tracker.remove_from_queue",
            new_callable=AsyncMock,
        ),
        patch("src.repositories.executions.create_execution", new_callable=AsyncMock),
        patch("src.repositories.executions.update_execution", new_callable=AsyncMock) as update_execution,
        patch(
            "src.jobs.consumers.workflow_execution.publish_execution_update",
            new_callable=AsyncMock,
        ),
        patch(
            "src.jobs.consumers.workflow_execution.publish_history_update",
            new_callable=AsyncMock,
        ),
    ):
        with pytest.raises(DomainFailureHandled, match="workflow setup failure"):
            await consumer.process_message(
                {
                    "execution_id": execution_id,
                    "code": "cHJpbnQoJ2hpJyk=",
                    "script_name": "inline.py",
                    "sync": True,
                }
            )

    update_execution.assert_awaited_once()
    consumer._pool.route_execution.assert_awaited_once()
    consumer._redis_client.delete_pending_execution.assert_awaited_once_with(execution_id)
    consumer._redis_client.push_result.assert_awaited_once_with(
        execution_id=execution_id,
        status="Failed",
        error="bad setup",
        error_type="ValueError",
        duration_ms=pytest.approx(0, abs=1000),
    )


@pytest.mark.asyncio
async def test_process_success_updates_storage_metrics_pubsub_and_sync_result() -> None:
    consumer = make_consumer()
    execution_id = str(uuid4())
    session = _Session()
    pending = {
        "workflow_id": "wf-1",
        "workflow_name": "Workflow",
        "org_id": "org-1",
        "user_id": "user-1",
        "user_name": "User One",
        "sync": True,
    }
    consumer._redis_client.get_pending_execution.return_value = pending
    consumer._redis_client.delete_pending_execution = AsyncMock()
    consumer._redis_client.push_result = AsyncMock()

    with (
        patch("src.core.database.get_session_factory", return_value=_session_factory(session)),
        patch("src.repositories.executions.update_execution", new_callable=AsyncMock) as update_execution,
        patch("src.services.events.processor.update_delivery_from_execution", new_callable=AsyncMock) as update_delivery,
        patch("bifrost._sync.flush_pending_changes", new_callable=AsyncMock, return_value=2) as flush_changes,
        patch("bifrost._logging.flush_logs_to_postgres", new_callable=AsyncMock, return_value=3) as flush_logs,
        patch("src.core.metrics.update_daily_metrics", new_callable=AsyncMock) as update_daily_metrics,
        patch("src.core.metrics.update_workflow_roi_daily", new_callable=AsyncMock) as update_workflow_roi_daily,
        patch("src.jobs.consumers.workflow_execution.publish_execution_update", new_callable=AsyncMock) as publish_execution_update,
        patch("src.jobs.consumers.workflow_execution.publish_history_update", new_callable=AsyncMock) as publish_history_update,
        patch("src.core.cache.cleanup_execution_cache", new_callable=AsyncMock) as cleanup_cache,
    ):
        await consumer._process_success(
            execution_id,
            {
                "success": True,
                "status": "Success",
                "result": {"ok": True},
                "duration_ms": 123,
                "variables": {"x": 1},
                "execution_context": {"ctx": True},
                "metrics": {"peak_memory_bytes": 10, "cpu_total_seconds": 0.25},
                "roi": {"time_saved": 4, "value": 12.5},
            },
        )

    update_execution.assert_awaited_once()
    assert update_execution.await_args.kwargs["execution_id"] == execution_id
    assert update_execution.await_args.kwargs["status"].value == "Success"
    assert update_execution.await_args.kwargs["result"] == {"ok": True}
    assert update_execution.await_args.kwargs["time_saved"] == 4
    assert update_execution.await_args.kwargs["value"] == 12.5
    update_delivery.assert_awaited_once_with(execution_id, "Success", session=session)
    flush_changes.assert_awaited_once_with(execution_id, session=session)
    flush_logs.assert_awaited_once_with(execution_id, session=session)
    update_daily_metrics.assert_awaited_once()
    assert update_daily_metrics.await_args.kwargs["peak_memory_bytes"] == 10
    update_workflow_roi_daily.assert_awaited_once_with(
        workflow_id="wf-1",
        org_id="org-1",
        status="Success",
        time_saved=4,
        value=12.5,
        db=session,
    )
    session.commit.assert_awaited_once()
    publish_execution_update.assert_awaited_once_with(
        execution_id,
        "Success",
        {"result": {"ok": True}, "durationMs": 123},
    )
    publish_history_update.assert_awaited_once()
    cleanup_cache.assert_awaited_once_with(execution_id)
    consumer._redis_client.delete_pending_execution.assert_awaited_once_with(execution_id)
    consumer._redis_client.push_result.assert_awaited_once_with(
        execution_id=execution_id,
        status="Success",
        result={"ok": True},
        error=None,
        error_type=None,
        duration_ms=123,
    )


@pytest.mark.asyncio
async def test_process_failure_maps_cancelled_status_and_emits_failure_event() -> None:
    consumer = make_consumer()
    execution_id = str(uuid4())
    session = _Session()
    pending = {
        "workflow_id": "wf-1",
        "workflow_name": "Workflow",
        "org_id": "org-1",
        "user_id": "user-1",
        "user_email": "user@example.com",
        "user_name": "User One",
        "sync": True,
        "event": {"type": "demo"},
    }
    consumer._redis_client.get_pending_execution.return_value = pending
    consumer._redis_client.delete_pending_execution = AsyncMock()
    consumer._redis_client.push_result = AsyncMock()

    with (
        patch("src.core.database.get_session_factory", return_value=_session_factory(session)),
        patch("src.repositories.executions.update_execution", new_callable=AsyncMock) as update_execution,
        patch("src.services.events.processor.update_delivery_from_execution", new_callable=AsyncMock) as update_delivery,
        patch("bifrost._sync.flush_pending_changes", new_callable=AsyncMock, return_value=0),
        patch("bifrost._logging.flush_logs_to_postgres", new_callable=AsyncMock, return_value=0),
        patch("src.core.metrics.update_daily_metrics", new_callable=AsyncMock) as update_daily_metrics,
        patch("src.jobs.consumers.workflow_execution.publish_execution_update", new_callable=AsyncMock) as publish_execution_update,
        patch("src.jobs.consumers.workflow_execution.publish_history_update", new_callable=AsyncMock) as publish_history_update,
        patch("src.core.cache.cleanup_execution_cache", new_callable=AsyncMock) as cleanup_cache,
        patch("src.services.events.builtins.emit_workflow_failure_events", new_callable=AsyncMock) as emit_failure,
    ):
        await consumer._process_failure(
            execution_id,
            {
                "success": False,
                "error": "cancelled",
                "error_type": "CancelledError",
                "duration_ms": 50,
            },
        )

    update_execution.assert_awaited_once()
    assert update_execution.await_args.kwargs["status"].value == "Cancelled"
    assert update_execution.await_args.kwargs["error_message"] == "cancelled"
    update_delivery.assert_awaited_once_with(
        execution_id,
        "Cancelled",
        error_message="cancelled",
        session=session,
    )
    update_daily_metrics.assert_awaited_once_with(
        org_id="org-1",
        status="Cancelled",
        duration_ms=50,
        workflow_id="wf-1",
        db=session,
    )
    session.commit.assert_awaited_once()
    publish_execution_update.assert_awaited_once_with(
        execution_id,
        "Cancelled",
        {"error": "cancelled", "errorType": "CancelledError"},
    )
    publish_history_update.assert_awaited_once()
    cleanup_cache.assert_awaited_once_with(execution_id)
    consumer._redis_client.delete_pending_execution.assert_awaited_once_with(execution_id)
    consumer._redis_client.push_result.assert_awaited_once_with(
        execution_id=execution_id,
        status="Cancelled",
        error="cancelled",
        error_type="CancelledError",
        duration_ms=50,
    )
    emit_failure.assert_awaited_once()
    assert emit_failure.await_args.kwargs["trigger_event"] == {"type": "demo"}
