from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

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
@pytest.mark.parametrize(
    ("kwargs", "detail"),
    [
        ({"verdict": "sideways"}, "Invalid verdict filter: sideways"),
        ({"metadata_filter": "not json"}, "metadata_filter must be valid JSON"),
        (
            {"metadata_filter": '{"key":"service","value":"helpdesk"}'},
            "metadata_filter must be a JSON array of {key,op,value} objects",
        ),
        (
            {"metadata_filter": '[{"key":"service"}]'},
            "each metadata_filter entry needs 'key' and 'value'",
        ),
        (
            {"metadata_filter": '[{"key":"service","op":"starts","value":"help"}]'},
            "Unsupported metadata_filter op: starts",
        ),
    ],
)
async def test_list_agent_runs_rejects_invalid_filter_inputs(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, str],
    detail: str,
) -> None:
    monkeypatch.setattr(agent_runs, "apply_agent_run_access", lambda query, user: query)
    params = {"verdict": None, "metadata_filter": None}
    params.update(kwargs)

    with pytest.raises(HTTPException) as exc:
        await agent_runs.list_agent_runs(
            _FakeDb([]),
            _user(),
            **params,
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == detail


@pytest.mark.asyncio
async def test_list_agent_runs_applies_filters_and_returns_paginated_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run(status="running", verdict=None)
    db = _FakeDb([2, [run]])
    monkeypatch.setattr(agent_runs, "apply_agent_run_access", lambda query, user: query)

    response = await agent_runs.list_agent_runs(
        db,
        _user(organization_id=run.org_id),
        agent_id=run.agent_id,
        status_filter="running",
        trigger_type="manual",
        org_id=run.org_id,
        start_date=run.created_at,
        end_date=run.created_at,
        q="ticket",
        verdict="unreviewed",
        metadata_filter='[{"key":"service","op":"eq","value":"helpdesk"}]',
        limit=10,
        offset=5,
    )

    assert response.total == 2
    assert [item.id for item in response.items] == [run.id]
    assert len(db.queries) == 2


@pytest.mark.asyncio
async def test_get_agent_run_raises_404_when_run_not_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid4()

    async def fake_load_agent_run_for_user(*args, **kwargs):
        return None

    monkeypatch.setattr(
        agent_runs,
        "load_agent_run_for_user",
        fake_load_agent_run_for_user,
    )

    with pytest.raises(HTTPException) as exc:
        await agent_runs.get_agent_run(run_id, _FakeDb([]), _user())

    assert exc.value.status_code == 404
    assert exc.value.detail == f"Agent run {run_id} not found"


@pytest.mark.asyncio
async def test_get_agent_run_returns_completed_run_with_db_steps_and_child_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run(steps=[_step(step_number=1), _step(step_number=2)])
    child_id = uuid4()
    child = SimpleNamespace(
        id=child_id,
        agent_id=uuid4(),
        agent=SimpleNamespace(name="Child Agent"),
        status="completed",
        asked="Do the delegated work",
        did="Completed it",
        answered="Done",
        duration_ms=25,
        created_at=datetime.now(timezone.utc),
    )
    db = _FakeDb([[], [child]])

    async def fake_load_agent_run_for_user(*args, **kwargs):
        return run

    monkeypatch.setattr(
        agent_runs,
        "load_agent_run_for_user",
        fake_load_agent_run_for_user,
    )

    response = await agent_runs.get_agent_run(run.id, db, _user())

    assert response.id == run.id
    assert response.child_run_ids == [child_id]
    assert response.child_runs[0].agent_name == "Child Agent"
    assert [step.step_number for step in response.steps] == [1, 2]
    assert response.ai_usage is None
    assert response.ai_totals is None


@pytest.mark.asyncio
async def test_get_agent_run_includes_ai_usage_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage = SimpleNamespace(
        provider="openai",
        model="gpt-test",
        input_tokens=10,
        output_tokens=20,
        cost=Decimal("0.030"),
        duration_ms=400,
        timestamp=datetime.now(timezone.utc),
        sequence=1,
    )
    totals = SimpleNamespace(
        total_input=10,
        total_output=20,
        total_cost=Decimal("0.030"),
        total_duration=400,
        call_count=1,
    )
    run = _run(steps=[])
    db = _FakeDb([[usage], totals, []])

    async def fake_load_agent_run_for_user(*args, **kwargs):
        return run

    monkeypatch.setattr(
        agent_runs,
        "load_agent_run_for_user",
        fake_load_agent_run_for_user,
    )

    response = await agent_runs.get_agent_run(run.id, db, _user())

    assert response.ai_usage is not None
    assert response.ai_usage[0].provider == "openai"
    assert response.ai_usage[0].cost == "0.030"
    assert response.ai_totals is not None
    assert response.ai_totals.total_input_tokens == 10
    assert response.ai_totals.call_count == 1


@pytest.mark.asyncio
async def test_get_agent_run_falls_back_to_db_steps_when_redis_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run(status="running", steps=[_step(step_number=3)])
    db = _FakeDb([[], []])

    async def fake_load_agent_run_for_user(*args, **kwargs):
        return run

    def fake_get_redis():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(
        agent_runs,
        "load_agent_run_for_user",
        fake_load_agent_run_for_user,
    )
    monkeypatch.setattr(agent_runs, "get_redis", fake_get_redis)

    response = await agent_runs.get_agent_run(run.id, db, _user())

    assert response.status == "running"
    assert [step.step_number for step in response.steps] == [3]


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

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value

    def one(self):
        return self.value


class _FakeDb:
    def __init__(self, values: list[object]) -> None:
        self.values = values
        self.queries = []

    async def execute(self, query):
        self.queries.append(query)
        return _FakeResult(self.values.pop(0))


def _user(**overrides):
    values = {
        "user_id": uuid4(),
        "email": "user@example.test",
        "name": "User",
        "organization_id": uuid4(),
        "is_superuser": False,
        "has_platform_admin_grant": lambda: False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _step(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": uuid4(),
        "run_id": uuid4(),
        "step_number": 1,
        "type": "reasoning",
        "content": {"message": "checked"},
        "tokens_used": 25,
        "duration_ms": 150,
        "created_at": now,
    }
    values.update(overrides)
    if "run_id" not in overrides:
        values["run_id"] = values["id"]
    return SimpleNamespace(**values)
