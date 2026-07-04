"""
Unit tests for Bifrost AI SDK module.

Tests message building and structured output parsing utilities.
Uses mocked dependencies for fast, isolated testing.
"""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel  # type: ignore[reportMissingImports]


class SampleResponse(BaseModel):
    """Sample Pydantic model for structured output tests."""
    answer: str
    confidence: float


class TestAIBuildMessages:
    """Test message building utility functions."""

    def test_build_messages_with_prompt_only(self):
        """Test _build_messages with only a prompt."""
        from bifrost.ai import _build_messages

        result = _build_messages("Hello!", None, None)

        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "Hello!"}

    def test_build_messages_with_system_and_prompt(self):
        """Test _build_messages with system and prompt."""
        from bifrost.ai import _build_messages

        result = _build_messages("Hello!", None, "You are helpful.")

        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "You are helpful."}
        assert result[1] == {"role": "user", "content": "Hello!"}

    def test_build_messages_with_messages_list(self):
        """Test _build_messages with message list."""
        from bifrost.ai import _build_messages

        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hi!"},
        ]
        result = _build_messages(None, messages, None)

        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "Be helpful."}
        assert result[1] == {"role": "user", "content": "Hi!"}

    def test_build_messages_with_system_overrides_messages_system(self):
        """Test _build_messages - system param replaces system in messages."""
        from bifrost.ai import _build_messages

        messages = [
            {"role": "system", "content": "Original system."},
            {"role": "user", "content": "Hi!"},
        ]
        result = _build_messages(None, messages, "New system.")

        # System from parameter should be first, original system should be filtered
        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "New system."}
        assert result[1] == {"role": "user", "content": "Hi!"}


class TestAIStructuredOutput:
    """Test structured output parsing."""

    def test_parse_structured_response_plain_json(self):
        """Test parsing plain JSON response."""
        from bifrost.ai import _parse_structured_response

        content = '{"answer": "test", "confidence": 0.9}'
        result = _parse_structured_response(content, SampleResponse)

        assert isinstance(result, SampleResponse)
        assert result.answer == "test"
        assert result.confidence == 0.9

    def test_parse_structured_response_markdown_code_block(self):
        """Test parsing JSON in markdown code block."""
        from bifrost.ai import _parse_structured_response

        content = '''```json
{"answer": "test", "confidence": 0.9}
```'''
        result = _parse_structured_response(content, SampleResponse)

        assert isinstance(result, SampleResponse)
        assert result.answer == "test"
        assert result.confidence == 0.9

    def test_parse_structured_response_invalid_json_raises(self):
        """Test parsing invalid JSON raises error."""
        from bifrost.ai import _parse_structured_response

        content = "not valid json"

        with pytest.raises(json.JSONDecodeError):
            _parse_structured_response(content, SampleResponse)


class TestAIKnowledgeContext:
    """Test RAG context injection helper."""

    @pytest.mark.asyncio
    async def test_inject_knowledge_context_appends_to_existing_system(self, monkeypatch):
        """Knowledge search results should be appended to an existing system message."""
        from bifrost.ai import _inject_knowledge_context
        from bifrost import knowledge as knowledge_module

        async def fake_search(query, namespace, scope, limit):
            assert query == "What is the refund policy?"
            assert namespace == ["policies"]
            assert scope == "org-1"
            assert limit == 5
            return [
                SimpleNamespace(content="Refunds are available for 30 days."),
                SimpleNamespace(content="Escalate exceptions to finance."),
            ]

        monkeypatch.setattr(knowledge_module, "search", fake_search)

        messages = [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "What is the refund policy?"},
        ]

        result = await _inject_knowledge_context(messages, ["policies"], "org-1")

        assert result[0]["role"] == "system"
        assert result[0]["content"].startswith("Be concise.")
        assert "Refunds are available for 30 days." in result[0]["content"]
        assert result[1] == messages[1]

    @pytest.mark.asyncio
    async def test_inject_knowledge_context_without_user_query_returns_original(
        self, monkeypatch
    ):
        """No user message means there is no query to search."""
        from bifrost.ai import _inject_knowledge_context
        from bifrost import knowledge as knowledge_module

        async def fail_search(*args, **kwargs):
            raise AssertionError("knowledge search should not run")

        monkeypatch.setattr(knowledge_module, "search", fail_search)
        messages = [{"role": "system", "content": "Only system."}]

        assert await _inject_knowledge_context(messages, ["policies"], None) is messages

    @pytest.mark.asyncio
    async def test_inject_knowledge_context_prepends_system_when_needed(
        self, monkeypatch
    ):
        """Knowledge context should create a system message if one is absent."""
        from bifrost.ai import _inject_knowledge_context
        from bifrost import knowledge as knowledge_module

        async def fake_search(*args, **kwargs):
            return [SimpleNamespace(content="Relevant document.")]

        monkeypatch.setattr(knowledge_module, "search", fake_search)

        result = await _inject_knowledge_context(
            [{"role": "user", "content": "Question?"}],
            ["default"],
            None,
        )

        assert result[0]["role"] == "system"
        assert "Relevant document." in result[0]["content"]


class TestAICompleteAndStream:
    """Test public AI SDK API methods with mocked Bifrost client."""

    @pytest.mark.asyncio
    async def test_complete_posts_messages_and_returns_response(self, monkeypatch):
        """Plain completion should POST to the SDK endpoint and return AIResponse."""
        from bifrost.ai import ai

        captured = {}

        class Response:
            is_success = True

            def json(self):
                return {
                    "content": "Hello",
                    "input_tokens": 3,
                    "output_tokens": 4,
                    "model": "gpt-test",
                }

        class Client:
            async def post(self, path, **kwargs):
                captured["path"] = path
                captured["kwargs"] = kwargs
                return Response()

        monkeypatch.setattr("bifrost.ai.get_client", lambda: Client())

        result = await ai.complete(
            "Say hello",
            system="Be friendly.",
            max_tokens=32,
            org_id="org-1",
            model="gpt-test",
            timeout=12.5,
        )

        assert result.content == "Hello"
        assert result.input_tokens == 3
        assert result.output_tokens == 4
        assert result.model == "gpt-test"
        assert captured["path"] == "/api/sdk/ai/complete"
        assert captured["kwargs"]["timeout"] == 12.5
        assert captured["kwargs"]["json"]["messages"] == [
            {"role": "system", "content": "Be friendly."},
            {"role": "user", "content": "Say hello"},
        ]
        assert captured["kwargs"]["json"]["max_tokens"] == 32
        assert captured["kwargs"]["json"]["org_id"] == "org-1"
        assert captured["kwargs"]["json"]["model"] == "gpt-test"

    @pytest.mark.asyncio
    async def test_complete_structured_output_parses_model(self, monkeypatch):
        """Structured completions should parse response content into the model."""
        from bifrost.ai import ai

        class Response:
            is_success = True

            def json(self):
                return {
                    "content": '{"answer": "four", "confidence": 0.99}',
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "model": "gpt-test",
                }

        class Client:
            async def post(self, path, **kwargs):
                assert "valid JSON matching this schema" in kwargs["json"]["messages"][0]["content"]
                return Response()

        monkeypatch.setattr("bifrost.ai.get_client", lambda: Client())

        result = await ai.complete("2+2?", response_format=SampleResponse)

        assert isinstance(result, SampleResponse)
        assert result.answer == "four"
        assert result.confidence == 0.99

    @pytest.mark.asyncio
    async def test_complete_requires_prompt_or_messages(self):
        """Completion requires either prompt or messages."""
        from bifrost.ai import ai

        with pytest.raises(ValueError, match="Either 'prompt' or 'messages'"):
            await ai.complete()

    @pytest.mark.asyncio
    async def test_complete_raises_runtime_error_with_detail(self, monkeypatch):
        """Completion failures should include API detail when available."""
        from bifrost.ai import ai

        class Response:
            is_success = False
            text = "raw failure"
            status_code = 400

            def json(self):
                return {"detail": "LLM not configured"}

        class Client:
            async def post(self, path, **kwargs):
                return Response()

        monkeypatch.setattr("bifrost.ai.get_client", lambda: Client())

        with pytest.raises(RuntimeError, match="LLM not configured"):
            await ai.complete("Hello")

    @pytest.mark.asyncio
    async def test_stream_yields_chunks_and_ignores_bad_lines(self, monkeypatch):
        """Streaming should parse SSE data lines and skip malformed JSON."""
        from bifrost.ai import ai

        class StreamResponse:
            async def aiter_lines(self):
                for line in [
                    "",
                    "event: ping",
                    "data: {bad json",
                    'data: {"content": "Hel"}',
                    'data: {"content": "lo"}',
                    'data: {"done": true, "input_tokens": 2, "output_tokens": 3}',
                    "data: [DONE]",
                ]:
                    yield line

        class StreamContext:
            async def __aenter__(self):
                return StreamResponse()

            async def __aexit__(self, *args):
                return False

        class Client:
            def stream(self, method, path, **kwargs):
                assert method == "POST"
                assert path == "/api/sdk/ai/stream"
                assert kwargs["json"]["messages"] == [
                    {"role": "user", "content": "Say hello"}
                ]
                return StreamContext()

        monkeypatch.setattr("bifrost.ai.get_client", lambda: Client())

        chunks = [chunk async for chunk in ai.stream("Say hello")]

        assert [chunk.content for chunk in chunks] == ["Hel", "lo", ""]
        assert [chunk.done for chunk in chunks] == [False, False, True]
        assert chunks[-1].input_tokens == 2
        assert chunks[-1].output_tokens == 3

    @pytest.mark.asyncio
    async def test_stream_requires_prompt_or_messages(self):
        """Streaming requires either prompt or messages."""
        from bifrost.ai import ai

        with pytest.raises(ValueError, match="Either 'prompt' or 'messages'"):
            [chunk async for chunk in ai.stream()]

    @pytest.mark.asyncio
    async def test_get_model_info_success_and_error(self, monkeypatch):
        """Model info should return JSON or raise an API-detail error."""
        from bifrost.ai import ai

        class SuccessResponse:
            is_success = True

            def json(self):
                return {"provider": "openai", "model": "gpt-test"}

        class ErrorResponse:
            is_success = False
            text = "raw failure"
            status_code = 500

            def json(self):
                return {"detail": "not configured"}

        class Client:
            def __init__(self):
                self.response: object = SuccessResponse()

            async def get(self, path):
                assert path == "/api/sdk/ai/info"
                return self.response

        client = Client()
        monkeypatch.setattr("bifrost.ai.get_client", lambda: client)

        assert await ai.get_model_info() == {"provider": "openai", "model": "gpt-test"}

        client.response = ErrorResponse()
        with pytest.raises(RuntimeError, match="not configured"):
            await ai.get_model_info()
