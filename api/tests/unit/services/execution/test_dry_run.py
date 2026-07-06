import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.execution import dry_run


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class _FakeSession:
    def __init__(self, results=None):
        self._results = list(results or [])
        self.added = []
        self.commits = 0

    async def execute(self, _stmt):
        return self._results.pop(0)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_exc):
        return False


class _SessionFactory:
    def __init__(self, *sessions):
        self._sessions = list(sessions)

    def __call__(self):
        return _SessionContext(self._sessions.pop(0))


class _FakeLLMClient:
    provider_name = "test-provider"

    def __init__(self, content):
        self.response = SimpleNamespace(
            content=content,
            model="response-model",
            input_tokens=17,
            output_tokens=11,
        )
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _step(step_type, number, content):
    return SimpleNamespace(type=step_type, step_number=number, content=content)


@pytest.mark.asyncio
async def test_evaluate_against_prompt_shapes_transcript_and_records_usage(monkeypatch):
    run_id = uuid4()
    org_id = uuid4()
    run = SimpleNamespace(
        id=run_id,
        org_id=org_id,
        input={"ticket": "slow workstation"},
        output={"decision": "restart service"},
    )
    steps = [
        _step(
            "tool_call",
            1,
            {"tool": "ninja.restart", "args": {"device": "pc-1"}, "result": "ok"},
        ),
        _step("reasoning", 2, {"thought": "service was hung"}),
        *[
            _step("reasoning", index, {"thought": f"extra-{index}"})
            for index in range(3, 46)
        ],
    ]
    first_session = _FakeSession([
        _ScalarResult(run),
        _ScalarsResult(steps),
    ])
    usage_session = _FakeSession()
    llm_client = _FakeLLMClient(
        json.dumps(
            {
                "would_still_decide_same": False,
                "reasoning": "r" * 700,
                "alternative_action": "a" * 700,
                "confidence": "1.7",
            }
        )
    )

    async def fake_get_tuning_client(_db):
        return llm_client, "resolved-model"

    monkeypatch.setattr(dry_run, "get_tuning_client", fake_get_tuning_client)

    result = await dry_run.evaluate_against_prompt(
        run_id=run_id,
        proposed_prompt="Always restart safely.",
        session_factory=_SessionFactory(first_session, usage_session),
    )

    assert result.would_still_decide_same is False
    assert result.reasoning == "r" * 500
    assert result.alternative_action == "a" * 500
    assert result.confidence == 1.0

    llm_call = llm_client.calls[0]
    assert llm_call["model"] == "resolved-model"
    assert llm_call["max_tokens"] == 600
    payload = json.loads(llm_call["messages"][1].content)
    assert payload["proposed_prompt"] == "Always restart safely."
    assert payload["original_input"] == run.input
    assert payload["original_output"] == run.output
    assert len(payload["transcript"]) == 40
    assert payload["transcript"][0] == {
        "role": "tool_call",
        "tool": "ninja.restart",
        "args": {"device": "pc-1"},
        "result": "ok",
    }
    assert payload["transcript"][1] == {
        "role": "agent_reasoning",
        "content": {"thought": "service was hung"},
    }

    assert usage_session.commits == 1
    usage = usage_session.added[0]
    assert usage.agent_run_id == run_id
    assert usage.organization_id == org_id
    assert usage.provider == "test-provider"
    assert usage.model == "response-model"
    assert usage.input_tokens == 17
    assert usage.output_tokens == 11
    assert usage.sequence == 8000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "expected_reasoning"),
    [
        ("not json", "Unable to evaluate (model returned invalid JSON)"),
        ("[]", "Unable to evaluate (model did not return a JSON object)"),
    ],
)
async def test_evaluate_against_prompt_fails_closed_on_unusable_model_output(
    monkeypatch, content, expected_reasoning
):
    run = SimpleNamespace(id=uuid4(), org_id=uuid4(), input="in", output="out")
    first_session = _FakeSession([
        _ScalarResult(run),
        _ScalarsResult([_step("reasoning", 1, "not-a-dict")]),
    ])
    usage_session = _FakeSession()
    llm_client = _FakeLLMClient(content)
    llm_client.response.model = None
    llm_client.response.input_tokens = None
    llm_client.response.output_tokens = None

    async def fake_get_tuning_client(_db):
        return llm_client, "fallback-model"

    monkeypatch.setattr(dry_run, "get_tuning_client", fake_get_tuning_client)

    result = await dry_run.evaluate_against_prompt(
        run_id=run.id,
        proposed_prompt="new prompt",
        session_factory=_SessionFactory(first_session, usage_session),
    )

    assert result == dry_run.DryRunResult(
        would_still_decide_same=True,
        reasoning=expected_reasoning,
        alternative_action=None,
        confidence=0.0,
    )
    payload = json.loads(llm_client.calls[0]["messages"][1].content)
    assert payload["transcript"] == [{"role": "agent_reasoning", "content": {}}]
    usage = usage_session.added[0]
    assert usage.model == "fallback-model"
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0


@pytest.mark.asyncio
async def test_evaluate_against_prompt_normalizes_empty_optional_fields(monkeypatch):
    run = SimpleNamespace(id=uuid4(), org_id=uuid4(), input={}, output={})
    first_session = _FakeSession([
        _ScalarResult(run),
        _ScalarsResult([]),
    ])
    usage_session = _FakeSession()
    llm_client = _FakeLLMClient(
        json.dumps(
            {
                "reasoning": None,
                "alternative_action": "",
                "confidence": object(),
            },
            default=str,
        )
    )

    async def fake_get_tuning_client(_db):
        return llm_client, "resolved-model"

    monkeypatch.setattr(dry_run, "get_tuning_client", fake_get_tuning_client)

    result = await dry_run.evaluate_against_prompt(
        run_id=run.id,
        proposed_prompt="new prompt",
        session_factory=_SessionFactory(first_session, usage_session),
    )

    assert result == dry_run.DryRunResult(
        would_still_decide_same=True,
        reasoning="",
        alternative_action=None,
        confidence=0.0,
    )
