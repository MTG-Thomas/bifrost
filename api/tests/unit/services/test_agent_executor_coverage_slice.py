"""Focused AgentExecutor branch coverage tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.agent_executor import AgentExecutor
from src.services.llm.base import ToolCallRequest


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def mock_session_factory(mock_session):
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


@pytest.fixture
def executor(mock_session_factory):
    return AgentExecutor(mock_session_factory)


@pytest.mark.asyncio
async def test_is_first_user_message_treats_missing_count_as_first(
    executor, mock_session
):
    result = MagicMock()
    result.scalar.return_value = None
    mock_session.execute.return_value = result

    assert await executor._is_first_user_message(uuid4()) is True


@pytest.mark.asyncio
async def test_is_first_user_message_false_when_prior_user_messages_exist(
    executor, mock_session
):
    result = MagicMock()
    result.scalar.return_value = 2
    mock_session.execute.return_value = result

    assert await executor._is_first_user_message(uuid4()) is False


@pytest.mark.asyncio
async def test_execute_tool_returns_unknown_error_for_failed_workflow_without_error(
    executor, mock_session
):
    workflow = SimpleNamespace(id=uuid4(), name="Demo Tool")
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = workflow
    mock_session.execute.return_value = query_result

    user = SimpleNamespace(
        id=uuid4(),
        email="user@example.com",
        name="Test User",
        is_superuser=False,
    )
    conversation = SimpleNamespace(user=user)
    agent = SimpleNamespace(organization_id=uuid4(), system_tools=[])

    with patch(
        "src.services.execution.service.execute_tool",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(
            status=SimpleNamespace(value="Failed"),
            result={"ignored": True},
            error=None,
        ),
    ) as execute_tool:
        result = await executor._execute_tool(
            ToolCallRequest(
                id="call_failed",
                name="Demo Tool",
                arguments={"value": 1},
            ),
            agent=agent,
            conversation=conversation,
            execution_id="exec-123",
        )

    execute_tool.assert_awaited_once()
    assert result.result is None
    assert result.error == "Unknown error"
    assert result.tool_call_id == "call_failed"


@pytest.mark.asyncio
async def test_execute_tool_wraps_workflow_lookup_exceptions(executor, mock_session):
    mock_session.execute.side_effect = RuntimeError("database unavailable")

    result = await executor._execute_tool(
        ToolCallRequest(id="call_error", name="broken_tool", arguments={})
    )

    assert result.result is None
    assert result.error == "database unavailable"
    assert result.tool_name == "broken_tool"


@pytest.mark.asyncio
async def test_execute_system_tool_returns_not_found_without_conversation(executor):
    result = await executor._execute_system_tool(
        ToolCallRequest(id="call_missing", name="missing_system_tool", arguments={}),
        agent=SimpleNamespace(organization_id=None),
        conversation=None,
    )

    assert result.result is None
    assert result.error == "System tool 'missing_system_tool' not found"


@pytest.mark.asyncio
async def test_execute_system_tool_serializes_content_only_result(executor):
    async def fake_tool(_context, **_kwargs):
        return SimpleNamespace(content=[{"type": "text", "text": "ok"}])

    with patch(
        "src.services.mcp_server.server.get_system_tool_function",
        return_value=fake_tool,
    ):
        result = await executor._execute_system_tool(
            ToolCallRequest(id="call_content", name="content_tool", arguments={}),
            agent=SimpleNamespace(organization_id=uuid4()),
            conversation=None,
        )

    assert result.error is None
    assert result.result == [{"type": "text", "text": "ok"}]


@pytest.mark.asyncio
async def test_execute_system_tool_stringifies_plain_result(executor):
    async def fake_tool(_context, **_kwargs):
        return {"plain": "dict result"}

    with patch(
        "src.services.mcp_server.server.get_system_tool_function",
        return_value=fake_tool,
    ):
        result = await executor._execute_system_tool(
            ToolCallRequest(id="call_plain", name="plain_tool", arguments={}),
            agent=SimpleNamespace(organization_id=None),
            conversation=None,
        )

    assert result.error is None
    assert result.result == "{'plain': 'dict result'}"
