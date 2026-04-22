"""Per-flag tuning conversation service.

Owns the assistant-side of the multi-turn tuning chat that hangs off a
flagged ``AgentRun``: appends the user's turn, calls the configured tuning
model with the run + history context, and persists the assistant reply.

This module exposes:

- :func:`get_or_create_conversation`: return the existing
  ``AgentRunFlagConversation`` for a run, or create an empty one.
- :func:`append_user_message_and_reply`: append a user turn, call the
  tuning LLM for a reply, persist both on the conversation's JSONB
  ``messages`` column, and record an ``AIUsage`` row for cost tracking.
- :func:`enqueue_tune_chat`: thin RabbitMQ publish helper used by the API
  router that accepts a new user message; the worker consumes the message
  and invokes :func:`append_user_message_and_reply`.
"""
import json
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.rabbitmq import publish_message
from src.models.orm.agent_run_flag_conversations import AgentRunFlagConversation
from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_usage import AIUsage
from src.services.execution.model_selection import get_tuning_client
from src.services.llm import LLMMessage

logger = logging.getLogger(__name__)

TUNE_CHAT_QUEUE = "agent-tuning-chat"


FLAG_DIAGNOSE_SYSTEM = """You help users refine AI agent prompts. Given a flagged agent run (one that produced a wrong result), the user's note about what went wrong, and the conversation so far, respond naturally:
- Ask a clarifying question if the note is ambiguous
- Diagnose the likely cause by pointing to the prompt, tool choice, or missing knowledge
- When you have enough info, propose a specific, minimal prompt change (as a diff — add/keep/remove blocks)
Don't propose changes if the user hasn't confirmed the issue. Always be specific. Never apologize — the user wants action."""


async def get_or_create_conversation(
    run_id: UUID, db: AsyncSession
) -> AgentRunFlagConversation:
    """Return the existing flag conversation for ``run_id``, or create an empty one.

    Uses a flush (not commit) so the caller controls the transaction boundary.
    """
    conv = (
        await db.execute(
            select(AgentRunFlagConversation).where(
                AgentRunFlagConversation.run_id == run_id
            )
        )
    ).scalar_one_or_none()
    if conv is None:
        now = datetime.now(timezone.utc)
        conv = AgentRunFlagConversation(
            id=uuid4(),
            run_id=run_id,
            messages=[],
            created_at=now,
            last_updated_at=now,
        )
        db.add(conv)
        await db.flush()
    return conv


async def append_user_message_and_reply(
    run_id: UUID, content: str, db: AsyncSession
) -> AgentRunFlagConversation:
    """Append a user turn, call the tuning LLM for a reply, persist both + AIUsage.

    Returns the updated conversation. Caller is responsible for the outer
    transaction lifetime; this function commits at the end so the reply is
    durable even if the caller later rolls back.
    """
    run = (
        await db.execute(select(AgentRun).where(AgentRun.id == run_id))
    ).scalar_one()

    conv = await get_or_create_conversation(run_id, db)

    now = datetime.now(timezone.utc)
    # SQLAlchemy JSONB mutation: rebuild the list and reassign so the
    # dirty-state is tracked. In-place ``.append`` does not flag the
    # attribute as modified on JSONB columns unless MutableList is used.
    messages = list(conv.messages or [])
    messages.append(
        {
            "kind": "user",
            "content": content,
            "at": now.isoformat(),
        }
    )

    # Build the LLM prompt. Keep it simple: input/output + conversation history.
    prompt_payload = {
        "agent_run": {
            "input": run.input,
            "output": run.output,
        },
        "history": messages,
    }
    llm_messages = [
        LLMMessage(role="system", content=FLAG_DIAGNOSE_SYSTEM),
        LLMMessage(role="user", content=json.dumps(prompt_payload, default=str)),
    ]

    llm_client, resolved_model = await get_tuning_client(db)
    response = await llm_client.complete(
        messages=llm_messages, model=resolved_model, max_tokens=1500
    )

    messages.append(
        {
            "kind": "assistant",
            "content": response.content or "",
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )

    conv.messages = messages
    conv.last_updated_at = datetime.now(timezone.utc)

    provider = getattr(llm_client, "provider_name", "unknown")
    model_name = getattr(response, "model", None) or resolved_model
    db.add(
        AIUsage(
            agent_run_id=run.id,
            organization_id=run.org_id,
            provider=provider,
            model=model_name,
            input_tokens=getattr(response, "input_tokens", 0) or 0,
            output_tokens=getattr(response, "output_tokens", 0) or 0,
            cost=None,
            timestamp=datetime.now(timezone.utc),
        )
    )
    await db.commit()
    await db.refresh(conv)
    return conv


async def enqueue_tune_chat(run_id: UUID, content: str) -> None:
    """Publish a tune-chat message for the agent-tuning-chat worker."""
    await publish_message(
        TUNE_CHAT_QUEUE, {"run_id": str(run_id), "content": content}
    )
