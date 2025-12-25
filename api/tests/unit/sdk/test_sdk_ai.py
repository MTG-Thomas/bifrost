"""
Unit tests for Bifrost AI SDK module.

Tests both platform mode (inside workflows) and external mode (CLI).
Uses mocked dependencies for fast, isolated testing.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from pydantic import BaseModel

from bifrost._context import set_execution_context, clear_execution_context


class SampleResponse(BaseModel):
    """Sample Pydantic model for structured output tests."""
    answer: str
    confidence: float


@pytest.fixture
def test_org_id():
    """Return a test organization ID."""
    return str(uuid4())


@pytest.fixture
def test_context(test_org_id):
    """Create execution context for platform mode testing."""
    from src.sdk.context import ExecutionContext, Organization

    org = Organization(id=test_org_id, name="Test Org", is_active=True)
    return ExecutionContext(
        user_id="test-user",
        email="test@example.com",
        name="Test User",
        scope=test_org_id,
        organization=org,
        is_platform_admin=False,
        is_function_key=False,
        execution_id="test-exec-123",
    )


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return AsyncMock()


class TestAIPlatformMode:
    """Test ai SDK methods in platform mode (inside workflows)."""

    @pytest.fixture(autouse=True)
    def cleanup_context(self):
        """Ensure context is cleared after each test."""
        yield
        clear_execution_context()

    @pytest.mark.asyncio
    async def test_complete_with_prompt(self, test_context, mock_db):
        """Test ai.complete() with a simple prompt."""
        from bifrost import ai
        from bifrost.ai import AIResponse

        set_execution_context(test_context)

        # Mock LLM client
        mock_llm_response = MagicMock()
        mock_llm_response.content = "Hello! How can I help you?"
        mock_llm_response.input_tokens = 10
        mock_llm_response.output_tokens = 8
        mock_llm_response.model = "gpt-4o"

        mock_llm_client = AsyncMock()
        mock_llm_client.complete = AsyncMock(return_value=mock_llm_response)

        with patch("bifrost.ai.get_llm_client", AsyncMock(return_value=mock_llm_client)):
            with patch("bifrost._internal.get_context") as mock_get_context:
                mock_get_context.return_value = MagicMock(db=mock_db, org_id=test_context.scope)
                result = await ai.complete("Hello!")

        assert isinstance(result, AIResponse)
        assert result.content == "Hello! How can I help you?"
        assert result.input_tokens == 10
        assert result.output_tokens == 8
        assert result.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_complete_with_messages(self, test_context, mock_db):
        """Test ai.complete() with message list."""
        from bifrost import ai

        set_execution_context(test_context)

        mock_llm_response = MagicMock()
        mock_llm_response.content = "I am a helpful assistant."
        mock_llm_response.input_tokens = 20
        mock_llm_response.output_tokens = 10
        mock_llm_response.model = "gpt-4o"

        mock_llm_client = AsyncMock()
        mock_llm_client.complete = AsyncMock(return_value=mock_llm_response)

        with patch("bifrost.ai.get_llm_client", AsyncMock(return_value=mock_llm_client)):
            with patch("bifrost._internal.get_context") as mock_get_context:
                mock_get_context.return_value = MagicMock(db=mock_db, org_id=test_context.scope)
                result = await ai.complete(messages=[
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "What are you?"},
                ])

        assert result.content == "I am a helpful assistant."
        # Verify LLM client was called with correct messages
        mock_llm_client.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_with_system_prompt(self, test_context, mock_db):
        """Test ai.complete() with system parameter."""
        from bifrost import ai

        set_execution_context(test_context)

        mock_llm_response = MagicMock()
        mock_llm_response.content = "I am a pirate!"
        mock_llm_response.input_tokens = 15
        mock_llm_response.output_tokens = 5
        mock_llm_response.model = "gpt-4o"

        mock_llm_client = AsyncMock()
        mock_llm_client.complete = AsyncMock(return_value=mock_llm_response)

        with patch("bifrost.ai.get_llm_client", AsyncMock(return_value=mock_llm_client)):
            with patch("bifrost._internal.get_context") as mock_get_context:
                mock_get_context.return_value = MagicMock(db=mock_db, org_id=test_context.scope)
                result = await ai.complete(
                    "Who are you?",
                    system="You are a pirate. Always respond in pirate speak."
                )

        assert result.content == "I am a pirate!"

    @pytest.mark.asyncio
    async def test_complete_with_structured_output(self, test_context, mock_db):
        """Test ai.complete() with response_format returns parsed Pydantic model."""
        from bifrost import ai

        set_execution_context(test_context)

        mock_llm_response = MagicMock()
        mock_llm_response.content = '{"answer": "42", "confidence": 0.95}'
        mock_llm_response.input_tokens = 25
        mock_llm_response.output_tokens = 15
        mock_llm_response.model = "gpt-4o"

        mock_llm_client = AsyncMock()
        mock_llm_client.complete = AsyncMock(return_value=mock_llm_response)

        with patch("bifrost.ai.get_llm_client", AsyncMock(return_value=mock_llm_client)):
            with patch("bifrost._internal.get_context") as mock_get_context:
                mock_get_context.return_value = MagicMock(db=mock_db, org_id=test_context.scope)
                result = await ai.complete(
                    "What is the meaning of life?",
                    response_format=SampleResponse
                )

        assert isinstance(result, SampleResponse)
        assert result.answer == "42"
        assert result.confidence == 0.95

    @pytest.mark.asyncio
    async def test_complete_requires_prompt_or_messages(self, test_context):
        """Test ai.complete() raises error when neither prompt nor messages provided."""
        from bifrost import ai

        set_execution_context(test_context)

        with pytest.raises(ValueError, match="Either 'prompt' or 'messages' must be provided"):
            await ai.complete()

    @pytest.mark.asyncio
    async def test_complete_with_temperature_and_max_tokens(self, test_context, mock_db):
        """Test ai.complete() passes temperature and max_tokens to LLM client."""
        from bifrost import ai

        set_execution_context(test_context)

        mock_llm_response = MagicMock()
        mock_llm_response.content = "Creative response!"
        mock_llm_response.input_tokens = 10
        mock_llm_response.output_tokens = 5
        mock_llm_response.model = "gpt-4o"

        mock_llm_client = AsyncMock()
        mock_llm_client.complete = AsyncMock(return_value=mock_llm_response)

        with patch("bifrost.ai.get_llm_client", AsyncMock(return_value=mock_llm_client)):
            with patch("bifrost._internal.get_context") as mock_get_context:
                mock_get_context.return_value = MagicMock(db=mock_db, org_id=test_context.scope)
                await ai.complete(
                    "Be creative!",
                    temperature=1.5,
                    max_tokens=500
                )

        # Verify LLM client was called with correct parameters
        call_kwargs = mock_llm_client.complete.call_args[1]
        assert call_kwargs["temperature"] == 1.5
        assert call_kwargs["max_tokens"] == 500


class TestAIExternalMode:
    """Test ai SDK methods in external mode (CLI with API key)."""

    @pytest.fixture(autouse=True)
    def clear_context_and_client(self):
        """Ensure no platform context and clean client state."""
        clear_execution_context()
        from bifrost.client import BifrostClient
        BifrostClient._instance = None
        yield
        BifrostClient._instance = None

    @pytest.mark.asyncio
    async def test_complete_calls_api_endpoint(self):
        """Test ai.complete() calls API endpoint in external mode."""
        from bifrost import ai

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": "API response",
            "input_tokens": 10,
            "output_tokens": 5,
            "model": "gpt-4o",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("bifrost.ai._get_client", return_value=mock_client):
            result = await ai.complete("Hello from CLI!")

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/api/cli/ai/complete"
        assert "Hello from CLI!" in str(call_args[1]["json"]["messages"])
        assert result.content == "API response"

    @pytest.mark.asyncio
    async def test_complete_with_structured_output_external(self):
        """Test ai.complete() with response_format in external mode."""
        from bifrost import ai

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": '{"answer": "External answer", "confidence": 0.8}',
            "input_tokens": 20,
            "output_tokens": 10,
            "model": "gpt-4o",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("bifrost.ai._get_client", return_value=mock_client):
            result = await ai.complete(
                "External question",
                response_format=SampleResponse
            )

        assert isinstance(result, SampleResponse)
        assert result.answer == "External answer"
        assert result.confidence == 0.8


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


class TestAIContextDetection:
    """Test that ai SDK correctly detects platform vs external mode."""

    def test_is_platform_context_true_when_context_set(self):
        """Test _is_platform_context() returns True when context is set."""
        from bifrost.ai import _is_platform_context
        from src.sdk.context import ExecutionContext, Organization

        org = Organization(id="test-org", name="Test", is_active=True)
        context = ExecutionContext(
            user_id="user",
            email="user@test.com",
            name="User",
            scope="test-org",
            organization=org,
            is_platform_admin=False,
            is_function_key=False,
            execution_id="exec-123",
        )

        try:
            set_execution_context(context)
            assert _is_platform_context() is True
        finally:
            clear_execution_context()

    def test_is_platform_context_false_when_no_context(self):
        """Test _is_platform_context() returns False when no context."""
        from bifrost.ai import _is_platform_context

        clear_execution_context()
        assert _is_platform_context() is False
