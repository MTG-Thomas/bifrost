from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.routers import agent_runs


def _run(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": uuid4(),
        "agent_id": uuid4(),
        "agent": SimpleNamespace(name="Support Agent"),
        "trigger_type": "manual",
        "trigger_source": "api",
        "conversation_id": uuid4(),
        "event_delivery_id": None,
        "input": {"ticket": 123},
        "output": {"status": "done"},
        "status": "completed",
        "error": None,
        "org_id": uuid4(),
        "caller_user_id": str(uuid4()),
        "caller_email": "caller@example.test",
        "caller_name": "Caller",
        "iterations_used": 2,
        "tokens_used": 300,
        "budget_max_iterations": 5,
        "budget_max_tokens": 1000,
        "duration_ms": 1200,
        "llm_model": "gpt-test",
        "asked": "What happened?",
        "did": "Checked the ticket",
        "answered": "Ticket is resolved",
        "run_metadata": {"service": "helpdesk"},
        "confidence": 0.95,
        "confidence_reason": "clear evidence",
        "summary_status": "completed",
        "summary_error": None,
        "verdict": "up",
        "verdict_note": "good",
        "verdict_set_at": now,
        "verdict_set_by": uuid4(),
        "created_at": now,
        "started_at": now,
        "completed_at": now,
        "parent_run_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_run_to_response_maps_agent_run_fields() -> None:
    run = _run()

    response = agent_runs._run_to_response(run)

    assert response.id == run.id
    assert response.agent_id == run.agent_id
    assert response.agent_name == "Support Agent"
    assert response.metadata == {"service": "helpdesk"}
    assert response.verdict == "up"
    assert response.parent_run_id is None


def test_run_to_response_handles_missing_agent_and_metadata() -> None:
    response = agent_runs._run_to_response(_run(agent=None, run_metadata=None))

    assert response.agent_name is None
    assert response.metadata == {}


def test_is_platform_admin_delegates_to_principal_grant() -> None:
    user = SimpleNamespace(has_platform_admin_grant=lambda: True)

    assert agent_runs._is_platform_admin(user) is True


@pytest.mark.asyncio
async def test_estimate_per_run_cost_uses_recent_usage_history() -> None:
    db = _FakeDb([Decimal("0.030"), 3])

    cost, basis = await agent_runs._estimate_per_run_cost(db)

    assert cost == Decimal("0.010")
    assert basis == "history"
    assert len(db.queries) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("total_cost", "usage_count"),
    [
        (Decimal("0"), 3),
        (Decimal("0.010"), 0),
    ],
)
async def test_estimate_per_run_cost_falls_back_without_history(
    total_cost: Decimal,
    usage_count: int,
) -> None:
    db = _FakeDb([total_cost, usage_count])

    cost, basis = await agent_runs._estimate_per_run_cost(db)

    assert cost == Decimal("0.002")
    assert basis == "fallback"


class _FakeResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar(self):
        return self.value


class _FakeDb:
    def __init__(self, values: list[object]) -> None:
        self.values = values
        self.queries = []

    async def execute(self, query):
        self.queries.append(query)
        return _FakeResult(self.values.pop(0))
