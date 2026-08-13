from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from mcp.shared.exceptions import MCPError

from src.services.mcp_server import tasks


@pytest.mark.asyncio
async def test_get_task_reads_canonical_requester_authorized_execution_route():
    runtime_context = object()
    body = {
        "execution_id": "exec-1",
        "status": "Success",
        "created_at": "2026-08-12T12:00:00+00:00",
        "completed_at": "2026-08-12T12:00:01+00:00",
        "result": {"changed": True},
        "result_type": "json",
    }

    with (
        patch.object(tasks, "_runtime_context", return_value=runtime_context),
        patch.object(tasks, "call_rest", new=AsyncMock(return_value=(200, body))) as call,
    ):
        result = await tasks.get_task_result("execution:exec-1")

    call.assert_awaited_once_with(
        runtime_context,
        "GET",
        "/api/executions/exec-1",
    )
    assert result.status == "completed"
    assert result.result == {
        "execution_id": "exec-1",
        "result": {"changed": True},
        "result_type": "json",
    }


@pytest.mark.asyncio
async def test_get_task_hides_missing_or_forbidden_durable_handle():
    with (
        patch.object(tasks, "_runtime_context", return_value=object()),
        patch.object(tasks, "call_rest", new=AsyncMock(return_value=(403, {}))),
        pytest.raises(MCPError, match="Task not found"),
    ):
        await tasks.get_task_result("agent-run:run-1")


@pytest.mark.asyncio
async def test_task_interceptor_returns_durable_handle_without_starting_a_worker():
    extension = tasks.BifrostTasksExtension()
    outcome = SimpleNamespace(
        structured_content={
            "durable_handle": {"kind": "platform-job", "id": "job-1"}
        }
    )
    context = SimpleNamespace(
        request_context=SimpleNamespace(protocol_version="2026-07-28"),
        client_extension_settings=lambda _identifier: {},
    )
    current = tasks.TaskResult(
        task_id="platform-job:job-1",
        status="working",
        created_at="2026-08-12T12:00:00+00:00",
        last_updated_at="2026-08-12T12:00:00+00:00",
        ttl_ms=None,
    )

    with patch.object(tasks, "get_task_result", new=AsyncMock(return_value=current)):
        result = await extension.intercept_tool_call(
            SimpleNamespace(name="bifrost_execute_tool"),
            context,
            AsyncMock(return_value=outcome),
        )

    assert result.model_dump(by_alias=True, exclude_none=True) == {
        "taskId": "platform-job:job-1",
        "status": "working",
        "createdAt": "2026-08-12T12:00:00+00:00",
        "lastUpdatedAt": "2026-08-12T12:00:00+00:00",
        "pollIntervalMs": 1000,
        "resultType": "task",
    }
    assert tasks.is_mcp_task_requested() is False


def test_task_result_serializer_preserves_extension_claim_fields():
    import mcp_types.methods as methods

    payload = tasks.CreateTaskResult(
        task_id="execution:exec-1",
        status="working",
        created_at="2026-08-12T12:00:00+00:00",
        last_updated_at="2026-08-12T12:00:00+00:00",
        ttl_ms=None,
    ).model_dump(by_alias=True, mode="json", exclude_none=True)

    tasks._install_task_result_serializer()
    try:
        serialized = methods.serialize_server_result(
            "tools/call",
            "2026-07-28",
            payload,
        )
    finally:
        tasks._uninstall_task_result_serializer()

    assert serialized["taskId"] == "execution:exec-1"
    assert serialized["resultType"] == "task"
