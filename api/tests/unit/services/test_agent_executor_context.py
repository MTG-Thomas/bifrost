"""
Unit tests for AgentExecutor context window management.

Tests token estimation, context pruning, and warning generation.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.agent_executor import (
    CONTEXT_KEEP_RECENT,
    CONTEXT_MAX_TOKENS,
    CONTEXT_WARNING_TOKENS,
    AgentExecutor,
)
from src.services.llm import LLMMessage, ToolCallRequest


@pytest.fixture
def mock_session():
    """Mock database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def executor(mock_session):
    """Create an AgentExecutor instance with mocked session."""
    return AgentExecutor(mock_session)


class TestTokenEstimation:
    """Test token estimation functionality."""

    def test_estimate_tokens_empty_messages(self, executor):
        """Test token estimation with empty message list."""
        result = executor._estimate_tokens([])
        assert result == 0

    def test_estimate_tokens_text_only(self, executor):
        """Test token estimation with text content only."""
        messages = [
            LLMMessage(role="system", content="Hello world"),  # 11 chars = ~2 tokens
            LLMMessage(role="user", content="How are you?"),  # 12 chars = ~3 tokens
        ]
        result = executor._estimate_tokens(messages)
        # (11 + 12) // 4 = 5 tokens
        assert result == 5

    def test_estimate_tokens_with_tool_calls(self, executor):
        """Test token estimation includes tool call JSON."""
        messages = [
            LLMMessage(
                role="assistant",
                content="Let me help",
                tool_calls=[
                    ToolCallRequest(
                        id="call_123",
                        name="search",
                        arguments={"query": "test"},
                    )
                ],
            ),
        ]
        result = executor._estimate_tokens(messages)
        # Should include both content and tool call JSON
        assert result > 0
        # Should be more than just the text content
        text_only = len("Let me help") // 4
        assert result > text_only

    def test_estimate_tokens_none_content(self, executor):
        """Test token estimation handles None content."""
        messages = [
            LLMMessage(role="assistant", content=None, tool_calls=None),
        ]
        result = executor._estimate_tokens(messages)
        assert result == 0

    def test_estimate_tokens_large_content(self, executor):
        """Test token estimation with large content."""
        # Create ~100K characters (should be ~25K tokens)
        large_content = "x" * 100_000
        messages = [LLMMessage(role="user", content=large_content)]
        result = executor._estimate_tokens(messages)
        assert result == 25_000  # 100000 // 4


class TestContextThresholds:
    """Test that context thresholds are properly configured."""

    def test_warning_threshold_less_than_max(self):
        """Verify warning threshold is less than max threshold."""
        assert CONTEXT_WARNING_TOKENS < CONTEXT_MAX_TOKENS

    def test_keep_recent_is_reasonable(self):
        """Verify keep_recent is a reasonable number."""
        assert CONTEXT_KEEP_RECENT >= 10
        assert CONTEXT_KEEP_RECENT <= 50

    def test_max_tokens_reasonable_for_claude(self):
        """Verify max tokens leaves headroom for Claude's 200K context."""
        assert CONTEXT_MAX_TOKENS <= 150_000  # Leave at least 50K for response


class TestContextPruning:
    """Test context pruning functionality."""

    @pytest.fixture
    def mock_llm_client(self):
        """Mock LLM client for summarization."""
        client = AsyncMock()
        client.complete = AsyncMock(
            return_value=MagicMock(content="Summary of previous conversation.")
        )
        return client

    @pytest.mark.asyncio
    async def test_prune_context_below_threshold(self, executor, mock_llm_client):
        """Test that pruning is skipped when below threshold."""
        # Create messages well under the limit
        messages = [
            LLMMessage(role="system", content="You are helpful"),
            LLMMessage(role="user", content="Hello"),
            LLMMessage(role="assistant", content="Hi there!"),
        ]

        result, original_tokens = await executor._prune_context(
            messages, mock_llm_client
        )

        # Should return original messages unchanged
        assert result == messages
        assert original_tokens < CONTEXT_MAX_TOKENS
        # LLM should not be called for summarization
        mock_llm_client.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_prune_context_above_threshold(self, executor, mock_llm_client):
        """Test that pruning occurs when above threshold."""
        # Create a large system message to exceed threshold
        large_content = "x" * (CONTEXT_MAX_TOKENS * 4 + 1000)  # Exceed threshold

        messages = [
            LLMMessage(role="system", content="System prompt"),
            LLMMessage(role="user", content="First question"),
            LLMMessage(role="assistant", content=large_content),
            LLMMessage(role="user", content="Follow up 1"),
            LLMMessage(role="assistant", content="Response 1"),
            LLMMessage(role="user", content="Follow up 2"),
            LLMMessage(role="assistant", content="Response 2"),
        ]

        result, original_tokens = await executor._prune_context(
            messages, mock_llm_client, keep_recent=2
        )

        # Should have pruned messages
        assert len(result) < len(messages)
        # Should have called LLM for summarization
        mock_llm_client.complete.assert_called_once()
        # First message should still be system prompt
        assert result[0].role == "system"
        # Should include first user message
        assert any(m.content == "First question" for m in result)
        # Should include summary message
        assert any("[Previous conversation summary]" in (m.content or "") for m in result)

    @pytest.mark.asyncio
    async def test_prune_context_preserves_system_prompt(
        self, executor, mock_llm_client
    ):
        """Test that system prompt is always preserved."""
        large_content = "x" * (CONTEXT_MAX_TOKENS * 4 + 1000)
        system_prompt = "You are a specialized assistant"

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content="Question 1"),
            LLMMessage(role="assistant", content=large_content),
            LLMMessage(role="user", content="Question 2"),
            LLMMessage(role="assistant", content="Answer 2"),
        ]

        result, _ = await executor._prune_context(
            messages, mock_llm_client, keep_recent=2
        )

        # System prompt must be first message
        assert result[0].role == "system"
        assert result[0].content == system_prompt

    @pytest.mark.asyncio
    async def test_prune_context_preserves_first_user_message(
        self, executor, mock_llm_client
    ):
        """Test that first user message is preserved for context."""
        large_content = "x" * (CONTEXT_MAX_TOKENS * 4 + 1000)
        first_user_msg = "My original question that provides important context"

        messages = [
            LLMMessage(role="system", content="System"),
            LLMMessage(role="user", content=first_user_msg),
            LLMMessage(role="assistant", content=large_content),
            LLMMessage(role="user", content="Follow up"),
            LLMMessage(role="assistant", content="Response"),
        ]

        result, _ = await executor._prune_context(
            messages, mock_llm_client, keep_recent=2
        )

        # First user message should be preserved
        assert any(m.content == first_user_msg for m in result)

    @pytest.mark.asyncio
    async def test_prune_context_preserves_recent_messages(
        self, executor, mock_llm_client
    ):
        """Test that recent messages are preserved."""
        large_content = "x" * (CONTEXT_MAX_TOKENS * 4 + 1000)

        messages = [
            LLMMessage(role="system", content="System"),
            LLMMessage(role="user", content="Old question"),
            LLMMessage(role="assistant", content=large_content),
            LLMMessage(role="user", content="Recent question 1"),
            LLMMessage(role="assistant", content="Recent answer 1"),
            LLMMessage(role="user", content="Recent question 2"),
            LLMMessage(role="assistant", content="Recent answer 2"),
        ]

        result, _ = await executor._prune_context(
            messages, mock_llm_client, keep_recent=4
        )

        # Last 4 messages should be preserved
        assert any(m.content == "Recent question 1" for m in result)
        assert any(m.content == "Recent answer 1" for m in result)
        assert any(m.content == "Recent question 2" for m in result)
        assert any(m.content == "Recent answer 2" for m in result)

    @pytest.mark.asyncio
    async def test_prune_context_not_enough_to_summarize(
        self, executor, mock_llm_client
    ):
        """Test that pruning is skipped when not enough messages to summarize."""
        # Even if tokens are high, if there aren't enough messages between
        # first user and recent, we shouldn't summarize
        large_content = "x" * (CONTEXT_MAX_TOKENS * 4 + 1000)

        messages = [
            LLMMessage(role="system", content="System"),
            LLMMessage(role="user", content=large_content),  # First user is large
        ]

        result, _ = await executor._prune_context(
            messages, mock_llm_client, keep_recent=5
        )

        # Should return original messages (nothing to summarize)
        assert result == messages
        mock_llm_client.complete.assert_not_called()


class TestSummarizeMessages:
    """Test message summarization functionality."""

    @pytest.fixture
    def mock_llm_client(self):
        """Mock LLM client for summarization."""
        client = AsyncMock()
        client.complete = AsyncMock(
            return_value=MagicMock(content="Summary of the conversation.")
        )
        return client

    @pytest.mark.asyncio
    async def test_summarize_messages_basic(self, executor, mock_llm_client):
        """Test basic message summarization."""
        messages = [
            LLMMessage(role="user", content="What is Python?"),
            LLMMessage(role="assistant", content="Python is a programming language."),
        ]

        result = await executor._summarize_messages(messages, mock_llm_client)

        assert result == "Summary of the conversation."
        mock_llm_client.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_summarize_messages_includes_tool_calls(
        self, executor, mock_llm_client
    ):
        """Test that tool calls are included in summarization input."""
        messages = [
            LLMMessage(
                role="assistant",
                content="I'll search for that.",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="search",
                        arguments={"query": "test"},
                    )
                ],
            ),
        ]

        await executor._summarize_messages(messages, mock_llm_client)

        # Check that the LLM was called with content mentioning the tool
        call_args = mock_llm_client.complete.call_args
        user_message = call_args.kwargs["messages"][1]
        assert "TOOL_CALL" in user_message.content
        assert "search" in user_message.content

    @pytest.mark.asyncio
    async def test_summarize_messages_includes_tool_results(
        self, executor, mock_llm_client
    ):
        """Test that tool results are included in summarization input."""
        messages = [
            LLMMessage(
                role="tool",
                content='{"result": "Found 5 items"}',
                tool_name="search",
            ),
        ]

        await executor._summarize_messages(messages, mock_llm_client)

        # Check that the LLM was called with content mentioning the tool result
        call_args = mock_llm_client.complete.call_args
        user_message = call_args.kwargs["messages"][1]
        assert "TOOL_RESULT" in user_message.content
        assert "search" in user_message.content

    @pytest.mark.asyncio
    async def test_summarize_messages_handles_empty_content(
        self, executor, mock_llm_client
    ):
        """Test handling of messages with None content."""
        mock_llm_client.complete.return_value = MagicMock(content=None)

        messages = [
            LLMMessage(role="user", content="Test"),
        ]

        result = await executor._summarize_messages(messages, mock_llm_client)

        # Should return empty string for None response
        assert result == ""
