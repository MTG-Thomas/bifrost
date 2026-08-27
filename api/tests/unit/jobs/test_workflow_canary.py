from __future__ import annotations

import pytest

from src.jobs.workflow_canary import canary_queue_name


def test_canary_queue_requires_isolated_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIFROST_WORKFLOW_QUEUE_NAME", "workflow-executions")
    with pytest.raises(ValueError, match="isolated"):
        canary_queue_name()


def test_canary_queue_accepts_dedicated_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "BIFROST_WORKFLOW_QUEUE_NAME", "workflow-executions-deployment-canary"
    )
    assert canary_queue_name() == "workflow-executions-deployment-canary"
