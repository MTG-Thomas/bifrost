"""Unit tests for the unscoped MCP agent gateway."""
import json

from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.orm.agents import Agent
from src.services.llm import ToolDefinition
from src.services.mcp_server.config_service import MCPConfig
from src.services.mcp_server.gateway import (
    AgentToolSnapshot,
    GatewayError,
    MCPAgentGatewayService,
    ResolvedGatewayTool,
)
from src.services.operation_receipts import (
    OperationReceiptClaim,
    OperationReceiptDisposition,
)
from src.services.mcp_server.server import MCPContext


def _context(*, is_platform_admin: bool = False) -> MCPContext:
    return MCPContext(
        user_id=uuid4(),
        org_id=uuid4(),
        is_platform_admin=is_platform_admin,
        user_email="robot@example.com",
        user_name="Robot",
    )


def _agent() -> MagicMock:
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.name = "Operations Agent"
    agent.description = "Handles operational tasks"
    agent.system_prompt = "Use the tools carefully."
    agent.is_active = True
    agent.system_tools = ["list_workflows"]
    agent.knowledge_sources = ["runbooks"]
    agent.delegated_agents = []
    agent.organization_id = uuid4()
    return agent


@pytest.mark.asyncio
async def test_snapshot_forwards_validated_platform_admin_claim():
    context = _context(is_platform_admin=True)
    service = MCPAgentGatewayService(context)
    agent = _agent()
    db = MagicMock()
    repo = MagicMock()
    repo.get_agent_with_access_check = AsyncMock(return_value=agent)

    @asynccontextmanager
    async def db_context():
        yield db

    with (
        patch.object(service, "_agent_repo", return_value=repo),
        patch("src.core.database.get_db_context", return_value=db_context()),
        patch(
            "src.services.mcp_server.gateway.resolve_agent_tools",
            new_callable=AsyncMock,
            return_value=([], {}),
        ) as resolve_tools,
        patch("src.services.mcp_server.gateway.MCPConfigService") as config_service,
    ):
        config_service.return_value.get_config = AsyncMock(
            return_value=MCPConfig()
        )
        await service.get_agent_snapshot(str(agent.id))

    resolve_tools.assert_awaited_once_with(
        agent,
        db,
        caller_user_id=context.user_id,
        caller_is_platform_admin=True,
    )


def _resolved_tool(
    *,
    name: str = "lookup_ticket",
    description: str = "Look up a ticket",
    parameters: dict | None = None,
) -> ResolvedGatewayTool:
    return ResolvedGatewayTool(
        tool_ref=str(uuid4()),
        definition=ToolDefinition(
            name=name,
            description=description,
            parameters=parameters
            or {
                "type": "object",
                "properties": {"ticket_id": {"type": "integer"}},
                "required": ["ticket_id"],
                "additionalProperties": False,
            },
        ),
        source="workflow",
        source_identity=f"workflow:{uuid4()}",
        source_id=uuid4(),
    )


def test_workflow_reference_is_stable_across_display_name_change():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    workflow_id = uuid4()

    first = service._resolve_gateway_tools(
        agent,
        [
            ToolDefinition(
                name="old_name",
                description="Old",
                parameters={"type": "object"},
            )
        ],
        {"old_name": workflow_id},
        MCPConfig(),
    )
    second = service._resolve_gateway_tools(
        agent,
        [
            ToolDefinition(
                name="new_name",
                description="New",
                parameters={"type": "object"},
            )
        ],
        {"new_name": workflow_id},
        MCPConfig(),
    )

    assert first[0].tool_ref == second[0].tool_ref
    assert first[0].source_identity == f"workflow:{workflow_id}"


def test_live_config_filters_underlying_tools_by_name_or_source_id():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    workflow_id = uuid4()
    definitions = [
        ToolDefinition(
            name="list_workflows",
            description="List",
            parameters={"type": "object"},
        ),
        ToolDefinition(
            name="lookup_ticket",
            description="Lookup",
            parameters={"type": "object"},
        ),
    ]

    tools = service._resolve_gateway_tools(
        agent,
        definitions,
        {"lookup_ticket": workflow_id},
        MCPConfig(allowed_tool_ids=[str(workflow_id)]),
    )

    assert [tool.definition.name for tool in tools] == ["lookup_ticket"]


def test_validation_error_is_model_repairable():
    tool = _resolved_tool()

    with pytest.raises(GatewayError) as exc_info:
        MCPAgentGatewayService.validate_arguments(
            tool,
            {"ticket_id": "not-an-integer", "surprise": True},
        )

    error = exc_info.value
    assert error.code == "INVALID_ARGUMENTS"
    assert error.retryable is True
    assert error.details["input_schema"] == tool.definition.parameters
    assert {issue["path"] for issue in error.details["issues"]} == {
        "/",
        "/ticket_id",
    }


def test_unknown_reference_does_not_fall_back_to_tool_name():
    agent = _agent()
    snapshot = AgentToolSnapshot(agent=agent, tools=[_resolved_tool()])

    with pytest.raises(GatewayError) as exc_info:
        MCPAgentGatewayService.find_tool(snapshot, "lookup_ticket")

    assert exc_info.value.code == "TOOL_NOT_FOUND_OR_FORBIDDEN"
    assert exc_info.value.retryable is True


def test_agent_hydration_never_implies_zero_matches_is_a_full_catalog():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    snapshot = AgentToolSnapshot(
        agent=agent,
        tools=[_resolved_tool(name="lookup_ticket"), _resolved_tool(name="close_ticket")],
    )

    result = service._search_agent_snapshot(
        snapshot,
        query=None,
        tool_ref=None,
        limit=10,
    )

    found = result["agents"][0]
    assert found["instructions"] == agent.system_prompt
    assert found["instructions_included"] is True
    assert found["matching_tools"] == []
    assert found["total_tools"] == 2
    assert found["returned_tools"] == 0
    assert found["complete"] is False
    assert "not the agent's full tool catalog" in found["search_again"]


def test_scoped_search_returns_only_matching_tools_with_disclosure_counts():
    service = MCPAgentGatewayService(_context())
    snapshot = AgentToolSnapshot(
        agent=_agent(),
        tools=[
            _resolved_tool(name="lookup_ticket"),
            _resolved_tool(
                name="send_invoice",
                description="Send an invoice",
                parameters={
                    "type": "object",
                    "properties": {"invoice_id": {"type": "integer"}},
                },
            ),
        ],
    )

    result = service._search_agent_snapshot(
        snapshot,
        query="ticket",
        tool_ref=None,
        limit=10,
    )

    found = result["agents"][0]
    assert [tool["name"] for tool in found["matching_tools"]] == ["lookup_ticket"]
    assert found["total_tools"] == 2
    assert found["returned_tools"] == 1
    assert found["complete"] is False
    assert found["total_matching_tools"] == 1
    assert found["has_more_matches"] is False
    assert result["response_complete"] is True


def test_exact_tool_hydration_includes_only_the_selected_live_schema():
    service = MCPAgentGatewayService(_context())
    tool = _resolved_tool()
    snapshot = AgentToolSnapshot(agent=_agent(), tools=[tool])

    result = service._search_agent_snapshot(
        snapshot,
        query=None,
        tool_ref=tool.tool_ref,
        limit=10,
    )

    found_tool = result["agents"][0]["matching_tools"][0]
    assert found_tool["tool_ref"] == tool.tool_ref
    assert found_tool["schema_included"] is True
    assert found_tool["input_schema"] == tool.definition.parameters
    assert found_tool["supports_async"] is True
    assert found_tool["default_async"] is False


def test_delegation_capability_declares_async_default():
    tool = replace(_resolved_tool(), source="delegation")

    compact = tool.compact()

    assert compact["source"] == "delegation"
    assert compact["supports_async"] is True
    assert compact["default_async"] is True


def test_execution_result_pages_large_values_without_invalid_json_truncation():
    result, page = MCPAgentGatewayService._page_execution_result(
        {
            "small": 42,
            "huge": {"rows": ["x" * 30_000]},
            "after": True,
        },
        result_path="",
        offset=0,
        limit=20,
    )

    assert result["small"] == 42
    assert result["huge"]["$omitted"] is True
    assert result["huge"]["path"] == "/huge"
    assert result["after"] is True
    assert page["has_more"] is False


def test_execution_result_path_can_hydrate_an_omitted_value():
    result, page = MCPAgentGatewayService._page_execution_result(
        {"huge": {"rows": list(range(50))}},
        result_path="/huge/rows",
        offset=10,
        limit=5,
    )

    assert result == [10, 11, 12, 13, 14]
    assert page["next_offset"] == 15
    assert page["has_more"] is True


@pytest.mark.parametrize(
    ("agent_status", "gateway_status"),
    [
        ("queued", "Pending"),
        ("running", "Running"),
        ("completed", "Success"),
        ("failed", "Failed"),
        ("timeout", "Timeout"),
        ("cancelled", "Cancelled"),
        ("budget_exceeded", "BudgetExceeded"),
    ],
)
def test_agent_run_status_uses_gateway_execution_vocabulary(
    agent_status,
    gateway_status,
):
    assert (
        MCPAgentGatewayService._agent_run_gateway_status(agent_status)
        == gateway_status
    )


@pytest.mark.asyncio
async def test_get_execution_authorizes_redis_only_pending_receipt():
    context = _context()
    service = MCPAgentGatewayService(context)
    execution_id = str(uuid4())
    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = None
    db.execute.return_value = db_result
    db_context = MagicMock()
    db_context.__aenter__ = AsyncMock(return_value=db)
    db_context.__aexit__ = AsyncMock(return_value=None)
    redis = MagicMock()
    redis.get_pending_execution = AsyncMock(
        return_value={
            "execution_id": execution_id,
            "workflow_id": str(uuid4()),
            "script_name": None,
            "user_id": str(context.user_id),
            "created_at": "2026-08-13T12:00:00+00:00",
        }
    )

    with (
        patch("src.core.database.get_db_context", return_value=db_context),
        patch("src.core.redis_client.get_redis_client", return_value=redis),
    ):
        result = await service.get_execution(execution_id)

    assert result["execution_id"] == execution_id
    assert result["status"] == "Pending"
    assert result["result_available"] is False


@pytest.mark.asyncio
async def test_get_execution_hides_another_users_redis_only_receipt():
    service = MCPAgentGatewayService(_context())
    execution_id = str(uuid4())
    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = None
    db.execute.return_value = db_result
    db_context = MagicMock()
    db_context.__aenter__ = AsyncMock(return_value=db)
    db_context.__aexit__ = AsyncMock(return_value=None)
    redis = MagicMock()
    redis.get_pending_execution = AsyncMock(
        return_value={"user_id": str(uuid4())}
    )

    with (
        patch("src.core.database.get_db_context", return_value=db_context),
        patch("src.core.redis_client.get_redis_client", return_value=redis),
    ):
        with pytest.raises(GatewayError) as exc_info:
            await service.get_execution(execution_id)

    assert exc_info.value.code == "EXECUTION_NOT_FOUND_OR_FORBIDDEN"


@pytest.mark.asyncio
async def test_execute_validates_before_dispatch():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = _resolved_tool()
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])

    with patch.object(service, "_dispatch", new_callable=AsyncMock) as dispatch:
        with pytest.raises(GatewayError) as exc_info:
            await service.execute_tool(
                snapshot,
                tool,
                {"ticket_id": "invalid"},
                operation_id="invalid-operation",
            )

    assert exc_info.value.code == "INVALID_ARGUMENTS"
    assert exc_info.value.details["agent_id"] == str(agent.id)
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_returns_auditable_envelope():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = _resolved_tool()
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])

    with patch.object(
        service,
        "_dispatch",
        new=AsyncMock(return_value={"ticket": 42}),
    ):
        result = await service.execute_tool(
            snapshot,
            tool,
            {"ticket_id": 42},
            operation_id="lookup-42",
        )

    assert result["agent_id"] == str(agent.id)
    assert result["tool_ref"] == tool.tool_ref
    assert result["tool_name"] == "lookup_ticket"
    assert result["source"] == "workflow"
    assert result["result"] == {"ticket": 42}
    assert isinstance(result["duration_ms"], int)


@pytest.mark.asyncio
async def test_workflow_dispatch_preserves_durable_metadata_and_domain_result():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = _resolved_tool()
    execution_id = str(uuid4())
    response = MagicMock()
    response.execution_id = execution_id
    response.status.value = "Success"
    response.duration_ms = 125
    response.result = {"ticket": 42}
    response.error = None
    response.error_type = None

    with patch(
        "src.services.execution.service.execute_tool",
        new=AsyncMock(return_value=response),
    ) as execute:
        result = await service._dispatch_workflow(
            agent,
            tool,
            {"ticket_id": 42},
            operation_id="workflow-operation",
            task_requested=False,
        )

    assert result == {
        "execution_id": execution_id,
        "status": "Success",
        "duration_ms": 125,
        "result": {"ticket": 42},
        "error": None,
        "error_type": None,
    }
    assert execute.await_args.kwargs["sync"] is True


@pytest.mark.asyncio
async def test_workflow_task_dispatch_returns_pending_durable_metadata():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = _resolved_tool()
    execution_id = str(uuid4())
    response = MagicMock()
    response.execution_id = execution_id
    response.status.value = "Pending"
    response.duration_ms = None
    response.result = None
    response.error = None
    response.error_type = None

    with patch(
        "src.services.execution.service.execute_tool",
        new=AsyncMock(return_value=response),
    ) as execute:
        result = await service._dispatch_workflow(
            agent,
            tool,
            {"ticket_id": 42},
            operation_id="workflow-task-operation",
            task_requested=True,
        )

    assert result["execution_id"] == execution_id
    assert result["status"] == "Pending"
    assert execute.await_args.kwargs["sync"] is False


@pytest.mark.asyncio
async def test_task_request_rejects_unsupported_tool_before_side_effect():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    workflow = _resolved_tool(name="list_workflows")
    tool = ResolvedGatewayTool(
        tool_ref=workflow.tool_ref,
        definition=workflow.definition,
        source="system",
        source_identity="system:list_workflows",
    )
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])

    with patch.object(service, "_dispatch", new_callable=AsyncMock) as dispatch:
        with pytest.raises(GatewayError) as exc_info:
            await service.execute_tool(
                snapshot,
                tool,
                {"ticket_id": 42},
                operation_id="unsupported-task",
                task_requested=True,
            )

    assert exc_info.value.code == "TASKS_UNSUPPORTED"
    dispatch.assert_not_awaited()


def test_operation_identity_is_stable_and_scoped_to_caller_agent_and_tool():
    first_context = _context()
    first = MCPAgentGatewayService(first_context)
    agent = _agent()
    tool = _resolved_tool()

    initial = first._operation_execution_id(agent, tool, "caller-operation-42")
    retry = first._operation_execution_id(agent, tool, "caller-operation-42")
    other_operation = first._operation_execution_id(agent, tool, "caller-operation-43")
    other_caller = MCPAgentGatewayService(_context())._operation_execution_id(
        agent,
        tool,
        "caller-operation-42",
    )

    assert initial == retry
    assert initial != other_operation
    assert initial != other_caller


def _receipt_claim(
    disposition: OperationReceiptDisposition,
    *,
    response: dict | None = None,
    error: dict | None = None,
    durable_handle: dict[str, str] | None = None,
) -> OperationReceiptClaim:
    return OperationReceiptClaim(
        receipt_id=uuid4(),
        disposition=disposition,
        owner_token=(
            uuid4() if disposition == OperationReceiptDisposition.OWNER else None
        ),
        response=response,
        error=error,
        durable_handle=durable_handle,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "task_requested", "dispatch_result"),
    [
        ("workflow", False, {"execution_id": "execution-1", "status": "Success"}),
        ("workflow", True, {"execution_id": "execution-2", "status": "Pending"}),
        ("delegation", False, {"status": "success", "answer": 42}),
        ("delegation", True, {"run_id": "run-1", "status": "queued"}),
        ("system", False, {"structured_content": {"created": True}}),
        ("knowledge", False, {"matches": []}),
        ("external_mcp", False, {"structured_content": {"created": True}}),
    ],
)
async def test_operation_receipt_replays_every_gateway_source_once(
    source,
    task_requested: bool,
    dispatch_result: dict,
):
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    base_tool = _resolved_tool()
    tool = ResolvedGatewayTool(
        tool_ref=base_tool.tool_ref,
        definition=base_tool.definition,
        source=source,
        source_identity=f"{source}:{base_tool.source_id}",
        source_id=base_tool.source_id,
        remote_tool_name="remote_lookup" if source == "external_mcp" else None,
    )
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])
    owner = _receipt_claim(OperationReceiptDisposition.OWNER)
    claim = AsyncMock(return_value=owner)
    complete = AsyncMock()
    record_handle = AsyncMock()
    dispatch = AsyncMock(return_value=dispatch_result)

    with (
        patch.object(
            service,
            "get_agent_snapshot",
            new=AsyncMock(return_value=snapshot),
        ),
        patch.object(service, "_dispatch", new=dispatch),
        patch(
            "src.services.mcp_server.gateway.claim_operation_receipt",
            new=claim,
        ),
        patch(
            "src.services.mcp_server.gateway.complete_operation_receipt_success",
            new=complete,
        ),
        patch(
            "src.services.mcp_server.gateway.record_operation_receipt_handle",
            new=record_handle,
        ),
    ):
        first = await service.execute_agent_tool(
            str(agent.id),
            tool.tool_ref,
            {"ticket_id": 42},
            operation_id="stable-operation",
            task_requested=task_requested,
        )
        claim.return_value = _receipt_claim(
            OperationReceiptDisposition.SUCCEEDED,
            response=first,
        )
        second = await service.execute_agent_tool(
            str(agent.id),
            tool.tool_ref,
            {"ticket_id": 42},
            operation_id="stable-operation",
            task_requested=task_requested,
        )

    assert second == first
    dispatch.assert_awaited_once()
    complete.assert_awaited_once()
    assert claim.await_count == 2
    assert (
        claim.await_args_list[0].kwargs["request_fingerprint"]
        == claim.await_args_list[1].kwargs["request_fingerprint"]
    )


@pytest.mark.asyncio
async def test_operation_receipt_replays_structured_gateway_error() -> None:
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = _resolved_tool()
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])
    owner = _receipt_claim(OperationReceiptDisposition.OWNER)
    claim = AsyncMock(return_value=owner)
    complete_error = AsyncMock()

    with (
        patch.object(
            service,
            "get_agent_snapshot",
            new=AsyncMock(return_value=snapshot),
        ),
        patch.object(
            service,
            "_dispatch",
            new=AsyncMock(
                side_effect=GatewayError(
                    "NEEDS_REAUTH",
                    "Reconnect the integration.",
                    retryable=True,
                    details={"reauth_url": "/connect"},
                )
            ),
        ) as dispatch,
        patch(
            "src.services.mcp_server.gateway.claim_operation_receipt",
            new=claim,
        ),
        patch(
            "src.services.mcp_server.gateway.complete_operation_receipt_error",
            new=complete_error,
        ),
    ):
        with pytest.raises(GatewayError) as first_error:
            await service.execute_agent_tool(
                str(agent.id),
                tool.tool_ref,
                {"ticket_id": 42},
                operation_id="failed-operation",
            )
        stored_error = complete_error.await_args.args[2]
        claim.return_value = _receipt_claim(
            OperationReceiptDisposition.FAILED,
            error=stored_error,
        )
        with pytest.raises(GatewayError) as replayed_error:
            await service.execute_agent_tool(
                str(agent.id),
                tool.tool_ref,
                {"ticket_id": 42},
                operation_id="failed-operation",
            )

    assert replayed_error.value.as_dict() == first_error.value.as_dict()
    dispatch.assert_awaited_once()
    complete_error.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("disposition", "expected_code"),
    [
        (OperationReceiptDisposition.MISMATCH, "OPERATION_ID_REUSED"),
        (
            OperationReceiptDisposition.STARTED,
            "OPERATION_IN_PROGRESS_OR_UNKNOWN",
        ),
    ],
)
async def test_non_owner_receipts_reject_without_dispatch(
    disposition: OperationReceiptDisposition,
    expected_code: str,
) -> None:
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = _resolved_tool()
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])
    claim = _receipt_claim(disposition)

    with (
        patch.object(
            service,
            "get_agent_snapshot",
            new=AsyncMock(return_value=snapshot),
        ),
        patch.object(service, "_dispatch", new_callable=AsyncMock) as dispatch,
        patch(
            "src.services.mcp_server.gateway.claim_operation_receipt",
            new=AsyncMock(return_value=claim),
        ),
        patch(
            "src.services.mcp_server.gateway.wait_for_operation_receipt",
            new=AsyncMock(return_value=claim),
        ),
        patch.object(
            service,
            "_recover_started_receipt",
            new=AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(GatewayError) as exc_info:
            await service.execute_agent_tool(
                str(agent.id),
                tool.tool_ref,
                {"ticket_id": 42},
                operation_id="contested-operation",
            )

    assert exc_info.value.code == expected_code
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_receipt_write_failure_recovers_from_durable_handle() -> None:
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = _resolved_tool()
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])
    durable_id = str(uuid4())
    handle = {"kind": "execution", "id": durable_id}
    owner = _receipt_claim(OperationReceiptDisposition.OWNER)
    started = _receipt_claim(
        OperationReceiptDisposition.STARTED,
        durable_handle=handle,
    )
    execute_result = {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "tool_ref": tool.tool_ref,
        "tool_name": tool.definition.name,
        "source": "workflow",
        "duration_ms": 1,
        "result": {"execution_id": durable_id, "status": "Pending"},
        "durable_handle": handle,
    }

    with (
        patch.object(
            service,
            "get_agent_snapshot",
            new=AsyncMock(return_value=snapshot),
        ),
        patch.object(
            service,
            "execute_tool",
            new=AsyncMock(return_value=execute_result),
        ) as execute,
        patch(
            "src.services.mcp_server.gateway.claim_operation_receipt",
            new=AsyncMock(side_effect=[owner, started]),
        ),
        patch(
            "src.services.mcp_server.gateway.wait_for_operation_receipt",
            new=AsyncMock(return_value=started),
        ),
        patch(
            "src.services.mcp_server.gateway.record_operation_receipt_handle",
            new=AsyncMock(),
        ) as record,
        patch(
            "src.services.mcp_server.gateway.complete_operation_receipt_success",
            new=AsyncMock(side_effect=RuntimeError("terminal receipt write lost")),
        ),
        patch(
            "src.services.mcp_server.tools._http_bridge.call_rest",
            new=AsyncMock(return_value=(200, {
                "execution_id": durable_id,
                "status": "Pending",
                "created_at": "2026-08-13T00:00:00+00:00",
            })),
        ),
        patch(
            "src.services.mcp_server.gateway.reconcile_operation_receipt_success",
            new=AsyncMock(return_value=True),
        ) as reconcile,
    ):
        with pytest.raises(RuntimeError, match="terminal receipt write lost"):
            await service.execute_agent_tool(
                str(agent.id),
                tool.tool_ref,
                {"ticket_id": 42},
                operation_id="recoverable-operation",
                task_requested=True,
            )
        recovered = await service.execute_agent_tool(
            str(agent.id),
            tool.tool_ref,
            {"ticket_id": 42},
            operation_id="recoverable-operation",
            task_requested=True,
        )

    execute.assert_awaited_once()
    record.assert_awaited_once()
    reconcile.assert_awaited_once()
    assert recovered["durable_handle"] == handle


@pytest.mark.asyncio
async def test_oversized_first_response_exactly_matches_bounded_replay() -> None:
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = _resolved_tool()
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])
    claim = AsyncMock(return_value=_receipt_claim(OperationReceiptDisposition.OWNER))
    complete = AsyncMock()
    dispatch = AsyncMock(
        return_value={"payload": ["x" * 5_000 for _ in range(100)], "token": "secret"}
    )

    with (
        patch.object(service, "get_agent_snapshot", new=AsyncMock(return_value=snapshot)),
        patch.object(service, "_dispatch", new=dispatch),
        patch("src.services.mcp_server.gateway.claim_operation_receipt", new=claim),
        patch(
            "src.services.mcp_server.gateway.complete_operation_receipt_success",
            new=complete,
        ),
        patch(
            "src.services.mcp_server.gateway.record_operation_receipt_handle",
            new=AsyncMock(),
        ),
    ):
        first = await service.execute_agent_tool(
            str(agent.id),
            tool.tool_ref,
            {"ticket_id": 42},
            operation_id="large-operation",
        )
        stored = complete.await_args.args[2]
        claim.return_value = _receipt_claim(
            OperationReceiptDisposition.SUCCEEDED,
            response=stored,
        )
        replay = await service.execute_agent_tool(
            str(agent.id),
            tool.tool_ref,
            {"ticket_id": 42},
            operation_id="large-operation",
        )

    assert first == stored == replay
    assert len(json.dumps(first).encode("utf-8")) <= 64 * 1024
    assert first["_receipt_payload_truncated"] is True
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_tombstone_refuses_redispatch() -> None:
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = _resolved_tool()
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])
    expired = _receipt_claim(OperationReceiptDisposition.EXPIRED)

    with (
        patch.object(service, "get_agent_snapshot", new=AsyncMock(return_value=snapshot)),
        patch.object(service, "_dispatch", new_callable=AsyncMock) as dispatch,
        patch(
            "src.services.mcp_server.gateway.claim_operation_receipt",
            new=AsyncMock(return_value=expired),
        ),
        patch.object(
            service,
            "_recover_started_receipt",
            new=AsyncMock(),
        ) as recover,
    ):
        with pytest.raises(GatewayError) as exc_info:
            await service.execute_agent_tool(
                str(agent.id),
                tool.tool_ref,
                {"ticket_id": 42},
                operation_id="expired-operation",
            )

    assert exc_info.value.code == "OPERATION_RESULT_EXPIRED"
    dispatch.assert_not_awaited()
    recover.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_rest_bridge_receives_scoped_operation_identity() -> None:
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    base_tool = _resolved_tool(name="list_workflows")
    tool = ResolvedGatewayTool(
        tool_ref=base_tool.tool_ref,
        definition=base_tool.definition,
        source="system",
        source_identity="system:list_workflows",
    )
    seen_context = None

    async def system_tool(context, **arguments):
        nonlocal seen_context
        seen_context = context
        return MagicMock(content=[], structured_content={"ok": True})

    with patch(
        "src.services.mcp_server.server.get_system_tool_function",
        return_value=system_tool,
    ):
        await service._dispatch_system_tool(
            agent,
            tool,
            {"ticket_id": 42},
            operation_id="raw-operation-id",
        )

    assert seen_context is not None
    assert seen_context.operation_id == service._operation_execution_id(
        agent,
        tool,
        "raw-operation-id",
    )
    assert seen_context.operation_id != "raw-operation-id"


def test_external_dispatch_prefers_the_structured_tool_payload():
    result = MCPAgentGatewayService.unwrap_external_result(
        {
            "content": [{"type": "text", "text": "fallback"}],
            "structured_content": {"tickets": [42]},
            "is_error": False,
            "_resolution_path": "user_token",
        }
    )

    assert result == {"tickets": [42]}


def test_external_dispatch_uses_content_without_structured_payload():
    content = [{"type": "text", "text": "plain result"}]

    result = MCPAgentGatewayService.unwrap_external_result(
        {
            "content": content,
            "structured_content": None,
            "is_error": False,
            "_resolution_path": "service_token",
        }
    )

    assert result == content


def test_external_dispatch_preserves_structured_error_details():
    underlying = {
        "content": [{"type": "text", "text": "vendor rejected request"}],
        "structured_content": {"error": "Invalid project"},
        "is_error": True,
        "_resolution_path": "user_token",
    }

    with pytest.raises(GatewayError) as exc_info:
        MCPAgentGatewayService.unwrap_external_result(underlying)

    error = exc_info.value
    assert error.message == "Invalid project"
    assert error.retryable is True
    assert error.details["underlying_result"] == underlying
