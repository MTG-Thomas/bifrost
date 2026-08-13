"""Unit tests for the unscoped MCP agent gateway."""

from contextlib import asynccontextmanager
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
    parameters: dict | None = None,
) -> ResolvedGatewayTool:
    return ResolvedGatewayTool(
        tool_ref=str(uuid4()),
        definition=ToolDefinition(
            name=name,
            description="Look up a ticket",
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
) -> OperationReceiptClaim:
    return OperationReceiptClaim(
        receipt_id=uuid4(),
        disposition=disposition,
        owner_token=(
            uuid4() if disposition == OperationReceiptDisposition.OWNER else None
        ),
        response=response,
        error=error,
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
