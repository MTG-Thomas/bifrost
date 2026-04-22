"""Post-run summarization — populates asked/did/confidence/metadata on an AgentRun.

This module owns:

- :func:`summarize_run`: load the completed run, render the input/output, ask
  the configured summarization model, and persist the parsed result onto
  ``AgentRun`` (asked/did/confidence/run_metadata/summary_status).
- :func:`enqueue_summarize`: thin RabbitMQ publish helper used by the
  ``agent-runs`` consumer once a run finishes.

Failure semantics: any error during the LLM call or JSON parsing is caught,
recorded on ``run.summary_error`` with ``summary_status='failed'``, and
swallowed. The handler in :mod:`src.jobs.summarize_worker` does the same
belt-and-suspenders so the message is never re-queued. The UI exposes a
regenerate button for recovery.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.jobs.rabbitmq import publish_message
from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_usage import AIUsage
from src.services.execution.model_selection import get_summarization_client
from src.services.llm import LLMMessage

logger = logging.getLogger(__name__)

SUMMARIZE_QUEUE = "agent-summarization"

SUMMARIZE_SYSTEM_PROMPT = """You summarize the behavior of an AI agent on a single run.
Given the agent's input and output, produce a JSON object with:
  - asked: one short sentence (<100 chars) describing what the user asked for, in the user's voice
  - did: one short sentence (<100 chars) describing what the agent did, third person
  - confidence: float 0.0-1.0 — how confident the agent's output appears to be
  - confidence_reason: one sentence explaining the confidence assessment
  - metadata: object of k/v pairs (string -> string) extracting notable entities (ticket IDs, customer names, severity, etc.) — max 8 entries
Return ONLY the JSON object, no prose."""


def _clamp_confidence(value: Any) -> float | None:
    """Clamp an LLM-returned confidence to [0.0, 1.0], or return ``None`` if invalid."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, f))


def _truncate(value: Any, max_len: int) -> str | None:
    """Coerce to non-empty truncated string, or ``None`` if blank/missing."""
    if value is None:
        return None
    s = str(value)[:max_len]
    return s or None


async def summarize_run(
    run_id: UUID, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Summarize a completed run. Idempotent on ``summary_status='completed'``.

    Skips runs that are not ``status='completed'`` (e.g. failed/cancelled),
    and runs that have already been summarized. Marks ``summary_status='failed'``
    on any LLM/parse error so the UI can surface a regenerate option.
    """
    # Phase 1: load + transition pending → generating, resolve LLM client
    async with session_factory() as db:
        run = (
            await db.execute(select(AgentRun).where(AgentRun.id == run_id))
        ).scalar_one_or_none()
        if run is None or run.status != "completed":
            return
        if run.summary_status == "completed":
            return  # idempotent

        run.summary_status = "generating"
        run.summary_error = None
        await db.commit()

        # Resolve LLM client + model BEFORE leaving the session
        # (model_selection takes the AsyncSession).
        llm_client, resolved_model = await get_summarization_client(db)

        # Snapshot fields we need for the prompt outside the session.
        run_input = run.input
        run_output = run.output
        org_id = run.org_id

    # Build the prompt as a JSON-serialized payload of input/output.
    user_content = json.dumps({"input": run_input, "output": run_output}, default=str)
    messages = [
        LLMMessage(role="system", content=SUMMARIZE_SYSTEM_PROMPT),
        LLMMessage(role="user", content=user_content),
    ]

    # Phase 2: call LLM (no DB connection held)
    try:
        response = await llm_client.complete(
            messages=messages,
            model=resolved_model,
            max_tokens=400,
        )
        parsed = json.loads(response.content or "")
    except json.JSONDecodeError as exc:
        logger.warning("Summarizer returned invalid JSON for run %s", run_id)
        async with session_factory() as db:
            run = (
                await db.execute(select(AgentRun).where(AgentRun.id == run_id))
            ).scalar_one()
            run.summary_status = "failed"
            run.summary_error = f"Invalid JSON from summarization model: {str(exc)[:200]}"
            await db.commit()
        return
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Summarizer LLM call failed for run %s", run_id)
        async with session_factory() as db:
            run = (
                await db.execute(select(AgentRun).where(AgentRun.id == run_id))
            ).scalar_one()
            run.summary_status = "failed"
            run.summary_error = f"LLM call failed: {str(exc)[:200]}"
            await db.commit()
        return

    if not isinstance(parsed, dict):
        async with session_factory() as db:
            run = (
                await db.execute(select(AgentRun).where(AgentRun.id == run_id))
            ).scalar_one()
            run.summary_status = "failed"
            run.summary_error = "Summarization model did not return a JSON object"
            await db.commit()
        return

    # Phase 3: persist success + AIUsage row
    async with session_factory() as db:
        run = (
            await db.execute(select(AgentRun).where(AgentRun.id == run_id))
        ).scalar_one()
        run.asked = _truncate(parsed.get("asked"), 400)
        run.did = _truncate(parsed.get("did"), 400)
        run.confidence = _clamp_confidence(parsed.get("confidence"))
        run.confidence_reason = _truncate(parsed.get("confidence_reason"), 500)

        md = parsed.get("metadata") or {}
        if isinstance(md, dict):
            extracted = {
                str(k): str(v)[:256]
                for k, v in md.items()
                if isinstance(v, (str, int, float))
            }
            existing = run.run_metadata or {}
            # Existing (agent-supplied) wins; LLM fills in gaps.
            merged = {**extracted, **existing}
            run.run_metadata = dict(list(merged.items())[:16])

        run.summary_generated_at = datetime.now(timezone.utc)
        run.summary_status = "completed"
        run.summary_error = None

        provider = getattr(llm_client, "provider_name", "unknown")
        model_name = getattr(response, "model", None) or resolved_model
        db.add(
            AIUsage(
                agent_run_id=run.id,
                organization_id=org_id,
                provider=provider,
                model=model_name,
                input_tokens=getattr(response, "input_tokens", 0) or 0,
                output_tokens=getattr(response, "output_tokens", 0) or 0,
                cost=None,
                timestamp=datetime.now(timezone.utc),
            )
        )
        await db.commit()


async def enqueue_summarize(run_id: UUID) -> None:
    """Publish a summarize message for the agent-summarization worker."""
    await publish_message(SUMMARIZE_QUEUE, {"run_id": str(run_id)})
