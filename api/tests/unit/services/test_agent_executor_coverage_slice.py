"""Focused AgentExecutor branch coverage tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.enums import MessageRole
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


@pytest.mark.asyncio
async def test_switch_agent_denies_missing_user_without_db_access(executor):
    conversation = SimpleNamespace(id=uuid4(), agent_id=uuid4())
    new_agent = SimpleNamespace(id=uuid4(), name="Restricted")

    with patch("src.services.agent_executor.AgentRepository") as repo_cls:
        chunk, switched = await executor._switch_agent(
            conversation,
            new_agent,
            "@mention",
            user=None,
        )

    repo_cls.assert_not_called()
    assert chunk is None
    assert switched is None
    assert conversation.agent_id != new_agent.id


@pytest.mark.asyncio
async def test_switch_agent_persists_only_access_checked_agent(executor, mock_session):
    conversation_id = uuid4()
    original_agent_id = uuid4()
    accessible_agent_id = uuid4()
    conversation = SimpleNamespace(id=conversation_id, agent_id=original_agent_id)
    requested_agent = SimpleNamespace(id=uuid4(), name="Requested")
    accessible_agent = SimpleNamespace(id=accessible_agent_id, name="Accessible")
    persisted_conversation = SimpleNamespace(id=conversation_id, agent_id=original_agent_id)
    user = SimpleNamespace(
        user_id=uuid4(),
        organization_id=uuid4(),
        is_superuser=False,
        is_external=False,
    )

    mock_session.get = AsyncMock(return_value=persisted_conversation)

    repo = MagicMock()
    repo.get_agent_with_access_check = AsyncMock(return_value=accessible_agent)
    with patch("src.services.agent_executor.AgentRepository", return_value=repo) as repo_cls:
        chunk, switched = await executor._switch_agent(
            conversation,
            requested_agent,
            "routed",
            user=user,
        )

    repo_cls.assert_called_once_with(
        mock_session,
        org_id=user.organization_id,
        user_id=user.user_id,
        is_superuser=False,
        is_external=False,
    )
    repo.get_agent_with_access_check.assert_awaited_once_with(requested_agent.id)
    mock_session.get.assert_awaited_once()
    assert persisted_conversation.agent_id == accessible_agent_id
    assert conversation.agent_id == accessible_agent_id
    assert switched is accessible_agent
    assert chunk.type == "agent_switch"
    assert chunk.agent_switch.agent_id == str(accessible_agent_id)
    assert chunk.agent_switch.reason == "routed"


@pytest.mark.asyncio
async def test_switch_agent_denies_inaccessible_agent(executor, mock_session):
    conversation = SimpleNamespace(id=uuid4(), agent_id=uuid4())
    requested_agent = SimpleNamespace(id=uuid4(), name="Requested")
    user = SimpleNamespace(
        user_id=uuid4(),
        organization_id=uuid4(),
        is_superuser=False,
        is_external=True,
    )
    repo = MagicMock()
    repo.get_agent_with_access_check = AsyncMock(return_value=None)

    with patch("src.services.agent_executor.AgentRepository", return_value=repo):
        chunk, switched = await executor._switch_agent(
            conversation,
            requested_agent,
            "routed",
            user=user,
        )

    assert chunk is None
    assert switched is None
    assert conversation.agent_id != requested_agent.id
    mock_session.get.assert_not_called()


@pytest.mark.asyncio
async def test_build_message_history_remaps_duplicate_tool_call_ids(
    executor,
    mock_session,
):
    conversation = SimpleNamespace(id=uuid4())
    db_messages = [
        SimpleNamespace(
            role=MessageRole.USER,
            content="run it",
            sequence=1,
            tool_calls=None,
            tool_call_id=None,
            tool_name=None,
            tool_input=None,
        ),
        SimpleNamespace(
            role=MessageRole.ASSISTANT,
            content="first",
            sequence=2,
            tool_calls=[{"id": "call_1", "name": "tool_a", "arguments": {"x": 1}}],
            tool_call_id=None,
            tool_name=None,
            tool_input=None,
        ),
        SimpleNamespace(
            role=MessageRole.TOOL,
            content="first result",
            sequence=3,
            tool_calls=None,
            tool_call_id="call_1",
            tool_name="tool_a",
            tool_input=None,
        ),
        SimpleNamespace(
            role=MessageRole.TOOL_CALL,
            content=None,
            sequence=4,
            tool_calls=None,
            tool_call_id="call_1",
            tool_name="tool_b",
            tool_input={"y": 2},
        ),
        SimpleNamespace(
            role=MessageRole.TOOL,
            content="second result",
            sequence=5,
            tool_calls=None,
            tool_call_id="call_1",
            tool_name="tool_b",
            tool_input=None,
        ),
        SimpleNamespace(
            role=MessageRole.SYSTEM,
            content="ignored extra system",
            sequence=6,
            tool_calls=None,
            tool_call_id=None,
            tool_name=None,
            tool_input=None,
        ),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = db_messages
    mock_session.execute.return_value = result

    with patch.object(
        executor,
        "_get_default_system_prompt",
        new=AsyncMock(return_value="Default prompt"),
    ):
        messages = await executor._build_message_history(None, conversation)

    assert [m.role for m in messages] == ["system", "user", "assistant", "tool", "tool"]
    assert messages[0].content == "Default prompt"
    assert messages[2].tool_calls[0].id == "call_1"
    assert messages[2].tool_calls[1].id == "call_1_t2"
    assert messages[3].tool_call_id == "call_1"
    assert messages[4].tool_call_id == "call_1_t2"


@pytest.mark.asyncio
async def test_build_message_history_creates_assistant_for_orphan_tool_call(
    executor,
    mock_session,
):
    conversation = SimpleNamespace(id=uuid4())
    orphan_tool_call = SimpleNamespace(
        role=MessageRole.TOOL_CALL,
        content=None,
        sequence=1,
        tool_calls=None,
        tool_call_id="orphan",
        tool_name="tool_without_assistant",
        tool_input={"ok": True},
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [orphan_tool_call]
    mock_session.execute.return_value = result

    with patch.object(
        executor,
        "_get_default_system_prompt",
        new=AsyncMock(return_value="Default prompt"),
    ):
        messages = await executor._build_message_history(None, conversation)

    assert [m.role for m in messages] == ["system", "assistant"]
    assert messages[1].content is None
    assert messages[1].tool_calls[0].id == "orphan"
    assert messages[1].tool_calls[0].arguments == {"ok": True}


@pytest.mark.asyncio
async def test_save_message_assigns_next_sequence_and_updates_conversation(
    executor,
    mock_session,
):
    conversation_id = uuid4()
    message_id = uuid4()
    conversation = SimpleNamespace(updated_at=None)
    max_result = MagicMock()
    max_result.scalar.return_value = 41
    conversation_result = MagicMock()
    conversation_result.scalar_one.return_value = conversation
    mock_session.execute.side_effect = [max_result, conversation_result]
    mock_session.add = MagicMock()

    message = await executor._save_message(
        conversation_id=conversation_id,
        role=MessageRole.TOOL_CALL,
        content=None,
        tool_call_id="call_1",
        tool_name="demo_tool",
        execution_id="exec_1",
        tool_state="running",
        tool_input={"value": 3},
        local_id="local-1",
        message_id=message_id,
    )

    mock_session.add.assert_called_once_with(message)
    assert message.id == message_id
    assert message.conversation_id == conversation_id
    assert message.sequence == 42
    assert message.tool_state == "running"
    assert message.tool_input == {"value": 3}
    assert message.local_id == "local-1"
    assert conversation.updated_at is not None


@pytest.mark.asyncio
async def test_update_tool_call_message_persists_result_state(
    executor,
    mock_session,
):
    message = SimpleNamespace(tool_state="running", tool_result=None, duration_ms=None)
    result = MagicMock()
    result.scalar_one.return_value = message
    mock_session.execute.return_value = result

    await executor._update_tool_call_message(
        message_id=uuid4(),
        tool_state="completed",
        tool_result={"answer": 42},
        duration_ms=123,
    )

    assert message.tool_state == "completed"
    assert message.tool_result == {"answer": 42}
    assert message.duration_ms == 123
