from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from mcp.shared.exceptions import MCPError

from src.services.mcp_server import tasks
from src.services.mcp_server.tool_result import tool_result_wire_payload
from src.services.mcp_server.tools.gateway import gateway_execute_result


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
        patch(
            "src.services.operation_receipts.read_operation_response_for_handle",
            new=AsyncMock(return_value={
                "agent_id": "agent-1",
                "agent_name": "Agent",
                "tool_ref": "tool-1",
                "tool_name": "Workflow",
                "source": "workflow",
                "duration_ms": 0,
            }),
        ),
    ):
        result = await tasks.get_task_result("execution:exec-1")

    call.assert_awaited_once_with(
        runtime_context,
        "GET",
        "/api/executions/exec-1",
    )
    assert result.status == "completed"
    assert result.created_at == "2026-08-12T12:00:00+00:00"
    assert result.result is not None
    expected_gateway_data = {
        "agent_id": "agent-1",
        "agent_name": "Agent",
        "tool_ref": "tool-1",
        "tool_name": "Workflow",
        "source": "workflow",
        "duration_ms": 0,
        "result": {
            "execution_id": "exec-1",
            "status": "Success",
            "result": {"changed": True},
            "result_type": "json",
            "error": None,
            "duration_ms": None,
            "durable_handle": {"kind": "execution", "id": "exec-1"},
        },
        "durable_handle": {"kind": "execution", "id": "exec-1"},
    }
    assert result.result == tool_result_wire_payload(
        gateway_execute_result(expected_gateway_data)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["execution", "agent-run", "platform-job"])
async def test_domain_failure_completes_with_call_tool_error_result(kind: str):
    durable_id = "failed-1"
    body = {
        "id": durable_id,
        "execution_id": durable_id,
        "status": "Failed" if kind == "execution" else "failed",
        "created_at": "2026-08-12T12:00:00+00:00",
        "completed_at": "2026-08-12T12:00:01+00:00",
        "error_message": "workflow failed",
        "error": {"code": "DOMAIN_FAILED", "message": "domain failed"},
    }
    with (
        patch.object(tasks, "_runtime_context", return_value=object()),
        patch.object(tasks, "call_rest", new=AsyncMock(return_value=(200, body))),
        patch(
            "src.services.operation_receipts.read_operation_response_for_handle",
            new=AsyncMock(return_value={"tool_name": "Tool", "agent_name": "Agent"}),
        ),
    ):
        result = await tasks.get_task_result(f"{kind}:{durable_id}")

    assert result.status == "completed"
    assert result.error is None
    assert result.result is not None
    assert result.result["isError"] is True
    assert set(result.result) == {
        "content",
        "structuredContent",
        "isError",
        "resultType",
    }


def test_protocol_failure_error_requires_json_rpc_shape():
    result = tasks.TaskResult(
        task_id="execution:exec-1",
        status="failed",
        created_at="2026-08-12T12:00:00+00:00",
        last_updated_at="2026-08-12T12:00:00+00:00",
        ttl_ms=None,
        error={"code": -32603, "message": "Protocol failure"},
    )

    assert result.model_dump(by_alias=True, exclude_none=True)["error"] == {
        "code": -32603,
        "message": "Protocol failure",
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
async def test_get_task_never_synthesizes_epoch_for_missing_created_at():
    with (
        patch.object(tasks, "_runtime_context", return_value=object()),
        patch.object(
            tasks,
            "call_rest",
            new=AsyncMock(return_value=(200, {"status": "Pending"})),
        ),
        pytest.raises(MCPError, match="Task not found"),
    ):
        await tasks.get_task_result("execution:exec-1")


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
        "ttlMs": 604800000.0,
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
