"""
AI SDK for Bifrost.

Provides Python API for LLM completions using platform-configured providers.
Supports structured outputs via Pydantic models and optional RAG integration.

Works in two modes:
1. Platform context (inside workflows): Direct LLM client access
2. External context (via dev API key): API calls to SDK endpoints

All methods are async and must be awaited.

Usage:
    from bifrost import ai

    # Simple completion
    response = await ai.complete("Summarize this: ...")
    print(response.content)

    # With messages
    response = await ai.complete(messages=[
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello!"},
    ])

    # Structured output with Pydantic
    from pydantic import BaseModel

    class Summary(BaseModel):
        title: str
        points: list[str]

    result = await ai.complete(
        "Summarize this article...",
        response_format=Summary
    )
    print(result.title)  # Typed!

    # With RAG context (searches knowledge before completion)
    response = await ai.complete(
        "What are our refund policies?",
        knowledge=["policies", "faq"]
    )

    # Streaming
    async for chunk in ai.stream("Write a story..."):
        print(chunk.content, end="")
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from ._context import _execution_context

logger = logging.getLogger(__name__)

# Type variable for structured outputs
T = TypeVar("T", bound=BaseModel)


@dataclass
class AIResponse:
    """
    Response from AI completion.

    Attributes:
        content: Text content from the model
        input_tokens: Number of input tokens used
        output_tokens: Number of output tokens generated
        model: Model identifier used
    """

    content: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None


@dataclass
class AIStreamChunk:
    """
    Streaming response chunk from AI.

    Attributes:
        content: Text content delta (None for non-delta chunks)
        done: True if this is the final chunk
        input_tokens: Token count (only in final chunk)
        output_tokens: Token count (only in final chunk)
    """

    content: str | None = None
    done: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None


def _is_platform_context() -> bool:
    """Check if running inside platform execution context."""
    return _execution_context.get() is not None


def _get_client():
    """Get the BifrostClient for API calls."""
    from .client import get_client
    return get_client()


def _build_messages(
    prompt: str | None,
    messages: list[dict[str, str]] | None,
    system: str | None,
) -> list[dict[str, str]]:
    """
    Build message list from various input formats.

    Args:
        prompt: Simple string prompt (becomes user message)
        messages: Pre-formatted message list
        system: System prompt to prepend

    Returns:
        List of message dicts with role and content
    """
    result: list[dict[str, str]] = []

    # Add system prompt if provided
    if system:
        result.append({"role": "system", "content": system})

    # Add messages or prompt
    if messages:
        # Filter out system messages if we already added one
        for msg in messages:
            if system and msg.get("role") == "system":
                continue
            result.append(msg)
    elif prompt:
        result.append({"role": "user", "content": prompt})

    return result


async def _inject_knowledge_context(
    messages: list[dict[str, str]],
    knowledge: list[str],
    org_id: str | None,
) -> list[dict[str, str]]:
    """
    Search knowledge namespaces and inject context into messages.

    Prepends relevant knowledge as a system message.
    """
    from . import knowledge as knowledge_module

    # Extract the user's question from the last user message
    user_query = None
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_query = msg.get("content")
            break

    if not user_query:
        return messages

    # Search knowledge
    results = await knowledge_module.search(
        user_query,
        namespace=knowledge,
        org_id=org_id,
        limit=5,
    )

    if not results:
        return messages

    # Build context from results
    context_parts = ["Relevant context from knowledge base:"]
    for doc in results:
        context_parts.append(f"\n---\n{doc.content}")

    knowledge_context = "\n".join(context_parts)

    # Find or create system message
    result = messages.copy()
    system_idx = next(
        (i for i, m in enumerate(result) if m.get("role") == "system"),
        None
    )

    if system_idx is not None:
        # Append to existing system message
        current = result[system_idx].get("content", "")
        result[system_idx] = {
            "role": "system",
            "content": f"{current}\n\n{knowledge_context}"
        }
    else:
        # Prepend new system message
        result.insert(0, {
            "role": "system",
            "content": knowledge_context
        })

    return result


def _build_structured_prompt(
    messages: list[dict[str, str]],
    response_format: type[BaseModel],
) -> list[dict[str, str]]:
    """
    Modify messages to request structured JSON output.

    Appends JSON schema instructions to the system message.
    """
    schema = response_format.model_json_schema()
    schema_str = json.dumps(schema, indent=2)

    instruction = (
        f"\n\nYou must respond with valid JSON matching this schema:\n"
        f"```json\n{schema_str}\n```\n"
        f"Respond ONLY with the JSON object, no additional text."
    )

    result = messages.copy()
    system_idx = next(
        (i for i, m in enumerate(result) if m.get("role") == "system"),
        None
    )

    if system_idx is not None:
        current = result[system_idx].get("content", "")
        result[system_idx] = {
            "role": "system",
            "content": f"{current}{instruction}"
        }
    else:
        result.insert(0, {
            "role": "system",
            "content": instruction
        })

    return result


def _parse_structured_response(
    content: str,
    response_format: type[T],
) -> T:
    """
    Parse LLM response into Pydantic model.

    Handles JSON extraction from markdown code blocks if present.
    """
    # Try to extract JSON from markdown code block
    text = content.strip()
    if text.startswith("```"):
        # Find the end of the code block
        lines = text.split("\n")
        json_lines = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            elif line.startswith("```") and in_block:
                break
            elif in_block:
                json_lines.append(line)
        text = "\n".join(json_lines)

    # Parse and validate
    data = json.loads(text)
    return response_format.model_validate(data)


class ai:
    """
    AI completion operations.

    Provides LLM completions using platform-configured providers.
    Supports structured outputs and RAG integration.
    """

    @staticmethod
    async def complete(
        prompt: str | None = None,
        *,
        messages: list[dict[str, str]] | None = None,
        system: str | None = None,
        response_format: type[T] | None = None,
        knowledge: list[str] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        org_id: str | None = None,
    ) -> AIResponse | T:
        """
        Generate an AI completion.

        Can be called with a simple prompt or a list of messages.
        Optionally returns structured output as a Pydantic model.

        Args:
            prompt: Simple text prompt (becomes a user message)
            messages: List of message dicts with "role" and "content"
            system: System prompt (prepended to messages)
            response_format: Pydantic model class for structured output
            knowledge: List of knowledge namespace(s) to search for context
            max_tokens: Override default max tokens
            temperature: Override default temperature (0.0-2.0)
            org_id: Organization scope for knowledge search

        Returns:
            AIResponse with content, or parsed Pydantic model if response_format provided

        Example:
            >>> from bifrost import ai
            >>> response = await ai.complete("Hello!")
            >>> print(response.content)

            >>> # Structured output
            >>> from pydantic import BaseModel
            >>> class Answer(BaseModel):
            ...     answer: str
            ...     confidence: float
            >>> result = await ai.complete(
            ...     "What is 2+2?",
            ...     response_format=Answer
            ... )
            >>> print(result.answer, result.confidence)
        """
        if prompt is None and messages is None:
            raise ValueError("Either 'prompt' or 'messages' must be provided")

        # Build message list
        msg_list = _build_messages(prompt, messages, system)

        if _is_platform_context():
            # Direct LLM client access (platform mode)
            from ._internal import get_context
            from src.services.llm import get_llm_client, LLMMessage

            context = get_context()
            target_org_id = org_id or getattr(context, 'org_id', None) or getattr(context, 'scope', None)

            # Inject knowledge context if requested
            if knowledge:
                msg_list = await _inject_knowledge_context(msg_list, knowledge, target_org_id)

            # Add structured output instructions
            if response_format:
                msg_list = _build_structured_prompt(msg_list, response_format)

            # Convert to LLMMessage objects
            llm_messages = [
                LLMMessage(role=msg["role"], content=msg["content"])  # type: ignore[arg-type]
                for msg in msg_list
            ]

            # Get LLM client and complete
            client = await get_llm_client(context.db)
            response = await client.complete(
                messages=llm_messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            logger.debug(
                f"ai.complete: model={response.model}, "
                f"tokens={response.input_tokens}/{response.output_tokens}"
            )

            # Parse structured response if requested
            if response_format and response.content:
                return _parse_structured_response(response.content, response_format)

            return AIResponse(
                content=response.content,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                model=response.model,
            )
        else:
            # API call (external mode)
            client = _get_client()

            # Inject knowledge context if requested
            if knowledge:
                msg_list = await _inject_knowledge_context(msg_list, knowledge, org_id)

            # Add structured output instructions
            if response_format:
                msg_list = _build_structured_prompt(msg_list, response_format)

            response = await client.post(
                "/api/cli/ai/complete",
                json={
                    "messages": msg_list,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "org_id": org_id,
                }
            )
            response.raise_for_status()
            data = response.json()

            # Parse structured response if requested
            if response_format and data.get("content"):
                return _parse_structured_response(data["content"], response_format)

            return AIResponse(
                content=data.get("content"),
                input_tokens=data.get("input_tokens"),
                output_tokens=data.get("output_tokens"),
                model=data.get("model"),
            )

    @staticmethod
    async def stream(
        prompt: str | None = None,
        *,
        messages: list[dict[str, str]] | None = None,
        system: str | None = None,
        knowledge: list[str] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        org_id: str | None = None,
    ) -> AsyncGenerator[AIStreamChunk, None]:
        """
        Generate a streaming AI completion.

        Yields chunks as they arrive from the LLM.

        Args:
            prompt: Simple text prompt (becomes a user message)
            messages: List of message dicts with "role" and "content"
            system: System prompt (prepended to messages)
            knowledge: List of knowledge namespace(s) to search for context
            max_tokens: Override default max tokens
            temperature: Override default temperature (0.0-2.0)
            org_id: Organization scope for knowledge search

        Yields:
            AIStreamChunk objects with content deltas

        Example:
            >>> from bifrost import ai
            >>> async for chunk in ai.stream("Write a story..."):
            ...     if chunk.content:
            ...         print(chunk.content, end="", flush=True)
            ...     if chunk.done:
            ...         print(f"\\nTokens: {chunk.input_tokens}/{chunk.output_tokens}")
        """
        if prompt is None and messages is None:
            raise ValueError("Either 'prompt' or 'messages' must be provided")

        # Build message list
        msg_list = _build_messages(prompt, messages, system)

        if _is_platform_context():
            # Direct LLM client access (platform mode)
            from ._internal import get_context
            from src.services.llm import get_llm_client, LLMMessage

            context = get_context()
            target_org_id = org_id or getattr(context, 'org_id', None) or getattr(context, 'scope', None)

            # Inject knowledge context if requested
            if knowledge:
                msg_list = await _inject_knowledge_context(msg_list, knowledge, target_org_id)

            # Convert to LLMMessage objects
            llm_messages = [
                LLMMessage(role=msg["role"], content=msg["content"])  # type: ignore[arg-type]
                for msg in msg_list
            ]

            # Get LLM client and stream
            client = await get_llm_client(context.db)

            async for chunk in client.stream(
                messages=llm_messages,
                max_tokens=max_tokens,
                temperature=temperature,
            ):
                if chunk.type == "delta":
                    yield AIStreamChunk(content=chunk.content)
                elif chunk.type == "done":
                    yield AIStreamChunk(
                        done=True,
                        input_tokens=chunk.input_tokens,
                        output_tokens=chunk.output_tokens,
                    )
                elif chunk.type == "error":
                    logger.error(f"ai.stream error: {chunk.error}")
                    raise RuntimeError(f"Streaming error: {chunk.error}")
        else:
            # API call with SSE (external mode)
            client = _get_client()

            # Inject knowledge context if requested
            if knowledge:
                msg_list = await _inject_knowledge_context(msg_list, knowledge, org_id)

            # Use SSE streaming endpoint
            async with client.stream(
                "POST",
                "/api/cli/ai/stream",
                json={
                    "messages": msg_list,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "org_id": org_id,
                }
            ) as response:
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue

                    data_str = line[6:]  # Remove "data: " prefix
                    if data_str == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                        if data.get("done"):
                            yield AIStreamChunk(
                                done=True,
                                input_tokens=data.get("input_tokens"),
                                output_tokens=data.get("output_tokens"),
                            )
                        else:
                            yield AIStreamChunk(content=data.get("content"))
                    except json.JSONDecodeError:
                        continue

    @staticmethod
    async def get_model_info() -> dict[str, Any]:
        """
        Get information about the configured LLM.

        Returns:
            Dict with provider, model, and configuration details

        Example:
            >>> info = await ai.get_model_info()
            >>> print(f"Using {info['provider']}/{info['model']}")
        """
        if _is_platform_context():
            from ._internal import get_context
            from src.services.llm.factory import get_llm_config

            context = get_context()
            config = await get_llm_config(context.db)

            return {
                "provider": config.provider,
                "model": config.model,
                "max_tokens": config.max_tokens,
                "temperature": config.temperature,
            }
        else:
            client = _get_client()
            response = await client.get("/api/cli/ai/info")
            response.raise_for_status()
            return response.json()
