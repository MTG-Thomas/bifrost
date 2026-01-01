"""
Coding Mode Models

Pydantic models for coding mode communication.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from src.models.contracts.agents import ToolCall, ToolProgress, ToolResult


class CodingModeSession(BaseModel):
    """Represents a coding mode session."""

    session_id: str
    user_id: str
    created_at: datetime
    last_activity: datetime


class AskUserQuestionOption(BaseModel):
    """Option for a user question from the SDK."""

    label: str
    description: str


class AskUserQuestion(BaseModel):
    """Question from SDK requiring user input."""

    question: str
    header: str
    options: list[AskUserQuestionOption]
    multi_select: bool = False


class CodingModeChunk(BaseModel):
    """
    Streaming chunk from coding mode.

    Designed to be compatible with existing ChatStreamChunk format
    so frontend can reuse existing components.

    Message boundary signals:
    - assistant_message_start: Emitted when an AssistantMessage begins
    - assistant_message_end: Emitted when an AssistantMessage is complete
      (all text and tool_call chunks for this message have been sent)

    This allows the frontend to know when to finalize a message segment,
    especially important for parallel tool execution where multiple tool_calls
    belong to the same message.
    """

    type: Literal[
        "session_start",
        "delta",
        "tool_call",
        "tool_progress",
        "tool_result",
        "done",
        "error",
        "ask_user_question",
        "assistant_message_start",
        "assistant_message_end",
    ]

    # Session info (for session_start)
    session_id: str | None = None

    # Text content (for delta)
    content: str | None = None

    # Nested objects to match ChatStreamChunk format (frontend expects these)
    tool_call: ToolCall | None = None
    tool_progress: ToolProgress | None = None
    tool_result: ToolResult | None = None
    execution_id: str | None = None

    # Error info (for error)
    error_message: str | None = None

    # Usage metrics (for done)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None

    # AskUserQuestion fields
    questions: list[AskUserQuestion] | None = None
    request_id: str | None = None

    # Message boundary fields (for assistant_message_end)
    # stop_reason indicates why the message ended: "tool_use" or "end_turn"
    stop_reason: str | None = None
