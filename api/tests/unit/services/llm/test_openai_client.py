from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.llm.base import LLMConfig, LLMMessage, ToolCallRequest, ToolDefinition
from src.services.llm.openai_client import OpenAIClient


def _client() -> OpenAIClient:
    client = OpenAIClient.__new__(OpenAIClient)
    client.config = LLMConfig(provider="openai", model="gpt-test", api_key="test-key")
    return client


def test_provider_name() -> None:
    assert _client().provider_name == "openai"


def test_convert_messages_handles_all_roles() -> None:
    client = _client()

    assert client._convert_messages([
        LLMMessage(role="system", content="system prompt"),
        LLMMessage(role="user", content="hello"),
        LLMMessage(
            role="assistant",
            content="calling",
            tool_calls=[
                ToolCallRequest(
                    id="call-1",
                    name="lookup",
                    arguments={"ticket": 123},
                )
            ],
        ),
        LLMMessage(role="tool", content='{"ok": true}', tool_call_id="call-1"),
    ]) == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "calling",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "arguments": '{"ticket": 123}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": '{"ok": true}'},
    ]


def test_convert_messages_omits_empty_assistant_content() -> None:
    client = _client()

    assert client._convert_messages([LLMMessage(role="assistant")]) == [
        {"role": "assistant"}
    ]


def test_convert_tools() -> None:
    client = _client()

    assert client._convert_tools([
        ToolDefinition(
            name="lookup",
            description="Lookup a ticket",
            parameters={"type": "object", "properties": {"id": {"type": "integer"}}},
        )
    ]) == [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Lookup a ticket",
                "parameters": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                },
            },
        }
    ]


@pytest.mark.asyncio
async def test_complete_parses_content_usage_and_tool_calls() -> None:
    calls: list[dict] = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        message=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    id="call-1",
                                    function=SimpleNamespace(
                                        name="lookup",
                                        arguments='{"ticket": 123}',
                                    ),
                                )
                            ],
                        ),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
                model="gpt-test",
            )

    client = _client()
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    response = await client.complete(
        [LLMMessage(role="user", content="hello")],
        tools=[ToolDefinition(name="lookup", description="Lookup", parameters={})],
        max_tokens=77,
        model="override-model",
    )

    assert calls == [
        {
            "model": "override-model",
            "messages": [{"role": "user", "content": "hello"}],
            "max_completion_tokens": 77,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "description": "Lookup",
                        "parameters": {},
                    },
                }
            ],
        }
    ]
    assert response.content is None
    assert response.finish_reason == "tool_calls"
    assert response.input_tokens == 10
    assert response.output_tokens == 3
    assert response.model == "gpt-test"
    assert response.tool_calls == [
        ToolCallRequest(id="call-1", name="lookup", arguments={"ticket": 123})
    ]


@pytest.mark.asyncio
async def test_complete_handles_missing_usage_and_no_tools() -> None:
    class FakeCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="done", tool_calls=None),
                    )
                ],
                usage=None,
                model="gpt-test",
            )

    client = _client()
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    response = await client.complete([LLMMessage(role="user", content="hello")])

    assert response.content == "done"
    assert response.tool_calls is None
    assert response.input_tokens is None
    assert response.output_tokens is None


@pytest.mark.asyncio
async def test_stream_yields_error_chunk_when_openai_call_fails() -> None:
    class FakeCompletions:
        async def create(self, **kwargs):
            raise RuntimeError("boom")

    client = _client()
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    chunks = [
        chunk
        async for chunk in client.stream([LLMMessage(role="user", content="hello")])
    ]

    assert len(chunks) == 1
    assert chunks[0].type == "error"
    assert chunks[0].error == "boom"
