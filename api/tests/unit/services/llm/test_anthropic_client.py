from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.llm.anthropic_client import AnthropicClient
from src.services.llm.base import LLMConfig, LLMMessage, ToolCallRequest, ToolDefinition


def _client() -> AnthropicClient:
    client = AnthropicClient.__new__(AnthropicClient)
    client.config = LLMConfig(provider="anthropic", model="claude-test", api_key="test-key")
    return client


def test_provider_name() -> None:
    assert _client().provider_name == "anthropic"


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
            "name": "lookup",
            "description": "Lookup a ticket",
            "input_schema": {
                "type": "object",
                "properties": {"id": {"type": "integer"}},
            },
        }
    ]


@pytest.mark.asyncio
async def test_complete_parses_text_tool_calls_usage_and_kwargs() -> None:
    calls: list[dict] = []

    class FakeMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                content=[
                    SimpleNamespace(type="text", text="hello"),
                    SimpleNamespace(
                        type="tool_use",
                        id="tool-1",
                        name="lookup",
                        input={"ticket": 123},
                    ),
                ],
                stop_reason="tool_use",
                usage=SimpleNamespace(input_tokens=11, output_tokens=4),
                model="claude-test",
            )

    client = _client()
    client.client = SimpleNamespace(messages=FakeMessages())

    response = await client.complete(
        [
            LLMMessage(role="system", content="system prompt"),
            LLMMessage(role="user", content="hello"),
        ],
        tools=[ToolDefinition(name="lookup", description="Lookup", parameters={})],
        max_tokens=55,
        model="override-model",
    )

    assert calls == [
        {
            "model": "override-model",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 55,
            "system": "system prompt",
            "tools": [
                {
                    "name": "lookup",
                    "description": "Lookup",
                    "input_schema": {},
                }
            ],
        }
    ]
    assert response.content == "hello"
    assert response.tool_calls == [
        ToolCallRequest(id="tool-1", name="lookup", arguments={"ticket": 123})
    ]
    assert response.finish_reason == "tool_use"
    assert response.input_tokens == 11
    assert response.output_tokens == 4
    assert response.model == "claude-test"


@pytest.mark.asyncio
async def test_complete_handles_no_text_or_tool_calls() -> None:
    class FakeMessages:
        async def create(self, **kwargs):
            return SimpleNamespace(
                content=[],
                stop_reason="stop",
                usage=SimpleNamespace(input_tokens=1, output_tokens=2),
                model="claude-test",
            )

    client = _client()
    client.client = SimpleNamespace(messages=FakeMessages())

    response = await client.complete([LLMMessage(role="user", content="hello")])

    assert response.content is None
    assert response.tool_calls is None
    assert response.finish_reason == "stop"


@pytest.mark.asyncio
async def test_stream_yields_delta_tool_call_and_done_chunks() -> None:
    class FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def __aiter__(self):
            self._events = iter([
                SimpleNamespace(
                    type="message_start",
                    message=SimpleNamespace(usage=SimpleNamespace(input_tokens=7)),
                ),
                SimpleNamespace(
                    type="content_block_start",
                    content_block=SimpleNamespace(
                        type="tool_use",
                        id="tool-1",
                        name="lookup",
                    ),
                ),
                SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(type="text_delta", text="partial"),
                ),
                SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(
                        type="input_json_delta",
                        partial_json='{"ticket": 123}',
                    ),
                ),
                SimpleNamespace(type="content_block_stop"),
                SimpleNamespace(
                    type="message_delta",
                    usage=SimpleNamespace(output_tokens=5),
                    delta=SimpleNamespace(stop_reason="tool_use"),
                ),
            ])
            return self

        async def __anext__(self):
            try:
                return next(self._events)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class FakeMessages:
        def stream(self, **kwargs):
            return FakeStream()

    client = _client()
    client.client = SimpleNamespace(messages=FakeMessages())

    chunks = [
        chunk
        async for chunk in client.stream([LLMMessage(role="user", content="hello")])
    ]

    assert [chunk.type for chunk in chunks] == ["delta", "tool_call", "done"]
    assert chunks[0].content == "partial"
    assert chunks[1].tool_call == ToolCallRequest(
        id="tool-1",
        name="lookup",
        arguments={"ticket": 123},
    )
    assert chunks[2].finish_reason == "tool_use"
    assert chunks[2].input_tokens == 7
    assert chunks[2].output_tokens == 5


@pytest.mark.asyncio
async def test_stream_yields_empty_args_for_invalid_tool_json() -> None:
    class FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def __aiter__(self):
            self._events = iter([
                SimpleNamespace(
                    type="content_block_start",
                    content_block=SimpleNamespace(
                        type="tool_use",
                        id="tool-1",
                        name="lookup",
                    ),
                ),
                SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(type="input_json_delta", partial_json="{bad"),
                ),
                SimpleNamespace(type="content_block_stop"),
            ])
            return self

        async def __anext__(self):
            try:
                return next(self._events)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class FakeMessages:
        def stream(self, **kwargs):
            return FakeStream()

    client = _client()
    client.client = SimpleNamespace(messages=FakeMessages())

    chunks = [
        chunk
        async for chunk in client.stream([LLMMessage(role="user", content="hello")])
    ]

    assert chunks == [
        chunks[0],
    ]
    assert chunks[0].type == "tool_call"
    assert chunks[0].tool_call == ToolCallRequest(
        id="tool-1",
        name="lookup",
        arguments={},
    )


@pytest.mark.asyncio
async def test_stream_yields_error_chunk_when_anthropic_call_fails() -> None:
    class FakeMessages:
        def stream(self, **kwargs):
            raise RuntimeError("boom")

    client = _client()
    client.client = SimpleNamespace(messages=FakeMessages())

    chunks = [
        chunk
        async for chunk in client.stream([LLMMessage(role="user", content="hello")])
    ]

    assert len(chunks) == 1
    assert chunks[0].type == "error"
    assert chunks[0].error == "boom"
