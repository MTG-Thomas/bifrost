"""Focused AgentExecutor branch coverage tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.contracts.agents import ToolResult
from src.models.enums import MessageRole
from src.services.agent_executor import AgentExecutor
from src.services.llm import LLMMessage
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


def _stream(*chunks):
    async def _generator(**_kwargs):
        for chunk in chunks:
            yield chunk

    return _generator


@pytest.mark.asyncio
async def test_chat_streams_final_text_and_records_usage(executor):
    conversation = SimpleNamespace(id=uuid4(), user_id=uuid4())
    user_msg = SimpleNamespace(id=uuid4())
    assistant_msg = SimpleNamespace(id=uuid4())
    llm_client = SimpleNamespace(
        model_name="test-model",
        provider_name="test-provider",
        stream=_stream(
            SimpleNamespace(type="delta", content="hel", tool_call=None),
            SimpleNamespace(type="delta", content="lo", tool_call=None),
            SimpleNamespace(
                type="done",
                content=None,
                tool_call=None,
                input_tokens=5,
                output_tokens=2,
            ),
        ),
    )
    saved_messages = AsyncMock(side_effect=[user_msg, assistant_msg])

    with (
        patch("src.services.agent_executor.get_llm_client", new=AsyncMock(return_value=llm_client)),
        patch("src.services.agent_router.AgentRouter") as router_cls,
        patch.object(executor, "_save_message", new=saved_messages),
        patch.object(
            executor,
            "_build_message_history",
            new=AsyncMock(return_value=[LLMMessage(role="system", content="prompt")]),
        ),
        patch.object(executor, "_record_ai_usage", new=AsyncMock()) as record_usage,
    ):
        router_cls.return_value.parse_mention = AsyncMock(return_value=None)
        chunks = [
            chunk
            async for chunk in executor.chat(
                agent=None,
                conversation=conversation,
                user_message="hello",
                enable_routing=False,
                local_id="local-1",
            )
        ]

    assert [chunk.type for chunk in chunks] == [
        "message_start",
        "delta",
        "delta",
        "done",
    ]
    assert chunks[0].user_message_id == str(user_msg.id)
    assert chunks[3].message_id == str(assistant_msg.id)
    assert chunks[0].local_id == "local-1"
    assert chunks[1].content == "hel"
    assert chunks[2].content == "lo"
    assert chunks[3].content == "hello"
    assert chunks[3].token_count_input == 5
    assert chunks[3].token_count_output == 2
    record_usage.assert_awaited_once()
    usage_kwargs = record_usage.await_args.kwargs
    assert usage_kwargs["provider"] == "test-provider"
    assert usage_kwargs["model"] == "test-model"
    assert usage_kwargs["organization_id"] is None
    assert usage_kwargs["user_id"] == conversation.user_id
    final_save_kwargs = saved_messages.await_args_list[-1].kwargs
    assert str(final_save_kwargs["message_id"]) == chunks[0].assistant_message_id


@pytest.mark.asyncio
async def test_chat_executes_tool_calls_and_remaps_duplicate_provider_ids(executor):
    conversation = SimpleNamespace(id=uuid4(), user_id=uuid4())
    agent = SimpleNamespace(
        id=uuid4(),
        name="Tool Agent",
        organization_id=uuid4(),
        llm_model="override-model",
        llm_max_tokens=321,
    )
    user_msg = SimpleNamespace(id=uuid4())
    text_msg = SimpleNamespace(id=uuid4())
    tool_call_msg = SimpleNamespace(id=uuid4())
    tool_msg = SimpleNamespace(id=uuid4())
    assistant_msg = SimpleNamespace(id=uuid4())
    saved_messages = AsyncMock(
        side_effect=[user_msg, text_msg, tool_call_msg, tool_msg, assistant_msg]
    )
    update_tool_call = AsyncMock()
    execute_tool = AsyncMock(
        return_value=ToolResult(
            tool_call_id="dup_iter1",
            tool_name="demo_tool",
            result={"ok": True},
            error=None,
            duration_ms=7,
        )
    )
    tool_call = ToolCallRequest(
        id="dup",
        name="demo_tool",
        arguments={"value": 1},
    )
    stream_calls = []

    async def stream(**kwargs):
        stream_calls.append(kwargs)
        if len(stream_calls) == 1:
            yield SimpleNamespace(type="delta", content="checking", tool_call=None)
            yield SimpleNamespace(type="tool_call", content=None, tool_call=tool_call)
            yield SimpleNamespace(
                type="done",
                content=None,
                tool_call=None,
                input_tokens=10,
                output_tokens=4,
            )
        else:
            yield SimpleNamespace(type="delta", content="complete", tool_call=None)
            yield SimpleNamespace(
                type="done",
                content=None,
                tool_call=None,
                input_tokens=3,
                output_tokens=2,
            )

    llm_client = SimpleNamespace(
        model_name="test-model",
        provider_name="test-provider",
        stream=stream,
    )
    history = [
        LLMMessage(role="system", content="prompt"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCallRequest(id="dup", name="old_tool", arguments={}),
            ],
        ),
    ]

    with (
        patch("src.services.agent_executor.get_llm_client", new=AsyncMock(return_value=llm_client)),
        patch("src.services.agent_router.AgentRouter") as router_cls,
        patch.object(executor, "_save_message", new=saved_messages),
        patch.object(executor, "_update_tool_call_message", new=update_tool_call),
        patch.object(executor, "_execute_tool", new=execute_tool),
        patch.object(executor, "_get_agent_tools", new=AsyncMock(return_value=[SimpleNamespace(name="demo_tool")])),
        patch.object(executor, "_build_message_history", new=AsyncMock(return_value=history)),
        patch.object(executor, "_record_ai_usage", new=AsyncMock()),
    ):
        router_cls.return_value.parse_mention = AsyncMock(return_value=None)
        chunks = [
            chunk
            async for chunk in executor.chat(
                agent=agent,
                conversation=conversation,
                user_message="use the tool",
                enable_routing=False,
            )
        ]

    assert len(stream_calls) == 2
    assert stream_calls[0]["model"] == "override-model"
    assert stream_calls[0]["max_tokens"] == 321
    assert tool_call.id == "dup_iter1"
    execute_tool.assert_awaited_once()
    assert execute_tool.await_args.args[0].id == "dup_iter1"
    update_tool_call.assert_awaited_once_with(
        message_id=tool_call_msg.id,
        tool_state="completed",
        tool_result={"ok": True},
        duration_ms=7,
    )
    assert saved_messages.await_args_list[2].kwargs["tool_call_id"] == "dup_iter1"
    assert saved_messages.await_args_list[3].kwargs["role"] == MessageRole.TOOL
    assert saved_messages.await_args_list[3].kwargs["content"] == '{"ok":true}'
    assert saved_messages.await_args_list[3].kwargs["tool_call_id"] == "dup_iter1"
    assert [chunk.type for chunk in chunks] == [
        "message_start",
        "delta",
        "tool_call",
        "assistant_message_end",
        "tool_call",
        "tool_progress",
        "tool_result",
        "delta",
        "done",
    ]
    assert chunks[-1].content == "complete"
    assert chunks[-1].token_count_input == 13
    assert chunks[-1].token_count_output == 6
