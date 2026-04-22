"""Run summarizer end-to-end with mocked LLM client.

Validates the Task 12 implementation: ``summarize_run`` loads a completed
``AgentRun``, asks the configured summarization model for a structured
extraction, and persists the parsed result onto the run record + an
``AIUsage`` row.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_usage import AIUsage
from src.services.execution.run_summarizer import (
    _clamp_confidence,
    summarize_run,
)


@pytest_asyncio.fixture
async def seed_completed_run(db_session, seed_agent):
    """Insert a completed AgentRun with input/output set, committed so a
    fresh session inside ``summarize_run`` can read it.

    Cleans up via the session_factory after the test (the row is committed
    past the ``db_session`` rollback boundary).
    """
    run = AgentRun(
        id=uuid4(),
        agent_id=seed_agent.id,
        trigger_type="test",
        status="completed",
        iterations_used=2,
        tokens_used=300,
        input={"message": "Reset my password"},
        output={"text": "Routed to Support"},
        summary_status="pending",
    )
    db_session.add(run)
    await db_session.commit()
    yield run
    # Manual cleanup since we committed past the rollback boundary.
    await db_session.execute(
        delete(AIUsage).where(AIUsage.agent_run_id == run.id)
    )
    await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
    await db_session.commit()


def _build_mock_llm_response(
    content: str,
    input_tokens: int = 200,
    output_tokens: int = 40,
    model: str = "claude-haiku-4-5",
):
    """Construct a non-async mock that quacks like a real LLMResponse."""
    response = MagicMock()
    response.content = content
    response.input_tokens = input_tokens
    response.output_tokens = output_tokens
    response.model = model
    return response


def _build_mock_client(response):
    """Construct a mock LLM client whose ``complete`` returns ``response``."""
    client = MagicMock()
    client.complete = AsyncMock(return_value=response)
    client.provider_name = "anthropic"
    return client


@pytest.mark.asyncio
async def test_summarize_run_populates_asked_did_confidence(
    async_session_factory, seed_completed_run
):
    """Happy path: LLM returns valid JSON; run gets fields populated, AIUsage row inserted."""
    from src.services.execution import run_summarizer as mod

    mock_resp = _build_mock_llm_response(
        '{"asked": "reset my password", "did": "routed to Support", '
        '"confidence": 0.9, "confidence_reason": "clear intent", '
        '"metadata": {"intent": "password_reset"}}'
    )
    mock_client = _build_mock_client(mock_resp)

    with patch.object(
        mod,
        "get_summarization_client",
        new=AsyncMock(return_value=(mock_client, "claude-haiku-4-5")),
    ):
        await summarize_run(seed_completed_run.id, async_session_factory)

    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == seed_completed_run.id)
            )
        ).scalar_one()
        assert run.asked == "reset my password"
        assert run.did == "routed to Support"
        assert run.confidence == 0.9
        assert run.confidence_reason == "clear intent"
        assert run.summary_status == "completed"
        assert run.summary_generated_at is not None
        # Metadata merged (LLM-extracted intent)
        assert run.run_metadata.get("intent") == "password_reset"
        usages = (
            (
                await db.execute(
                    select(AIUsage).where(AIUsage.agent_run_id == run.id)
                )
            )
            .scalars()
            .all()
        )
        assert any(u.model == "claude-haiku-4-5" for u in usages)


@pytest.mark.asyncio
async def test_summarize_run_invalid_json_marks_failed(
    async_session_factory, seed_completed_run
):
    """LLM returns garbage; run.summary_status = 'failed', summary_error stored."""
    from src.services.execution import run_summarizer as mod

    mock_client = _build_mock_client(_build_mock_llm_response("not json at all"))

    with patch.object(
        mod,
        "get_summarization_client",
        new=AsyncMock(return_value=(mock_client, "claude-haiku-4-5")),
    ):
        await summarize_run(seed_completed_run.id, async_session_factory)

    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == seed_completed_run.id)
            )
        ).scalar_one()
        assert run.summary_status == "failed"
        assert run.summary_error is not None
        assert "JSON" in run.summary_error or "json" in run.summary_error


@pytest.mark.asyncio
async def test_summarize_run_idempotent_when_completed(
    async_session_factory, seed_completed_run
):
    """Already-summarized run returns immediately; no LLM call."""
    from src.services.execution import run_summarizer as mod

    # Pre-mark as completed
    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == seed_completed_run.id)
            )
        ).scalar_one()
        run.summary_status = "completed"
        await db.commit()

    mock_client = _build_mock_client(_build_mock_llm_response("{}"))
    with patch.object(
        mod,
        "get_summarization_client",
        new=AsyncMock(return_value=(mock_client, "claude-haiku-4-5")),
    ):
        await summarize_run(seed_completed_run.id, async_session_factory)

    mock_client.complete.assert_not_called()


@pytest.mark.asyncio
async def test_summarize_run_skipped_when_run_not_completed(
    async_session_factory, db_session, seed_agent
):
    """If the run's status is not 'completed', summarize_run returns early without calling LLM."""
    from src.services.execution import run_summarizer as mod

    run = AgentRun(
        id=uuid4(),
        agent_id=seed_agent.id,
        trigger_type="test",
        status="failed",
        iterations_used=0,
        tokens_used=0,
        summary_status="pending",
    )
    db_session.add(run)
    await db_session.commit()

    try:
        mock_client = _build_mock_client(_build_mock_llm_response("{}"))
        with patch.object(
            mod,
            "get_summarization_client",
            new=AsyncMock(return_value=(mock_client, "claude-haiku-4-5")),
        ):
            await summarize_run(run.id, async_session_factory)
        mock_client.complete.assert_not_called()
    finally:
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
        await db_session.commit()


def test_clamp_confidence():
    assert _clamp_confidence(0.5) == 0.5
    assert _clamp_confidence(0.0) == 0.0
    assert _clamp_confidence(1.0) == 1.0
    assert _clamp_confidence(1.5) == 1.0  # clamped
    assert _clamp_confidence(-0.2) == 0.0
    assert _clamp_confidence(None) is None
    assert _clamp_confidence("not a number") is None
    assert _clamp_confidence("0.7") == 0.7  # numeric string parsed
