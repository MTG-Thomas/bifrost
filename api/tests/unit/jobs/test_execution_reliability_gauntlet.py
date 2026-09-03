from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from scripts import execution_reliability_gauntlet as gauntlet


def test_gauntlet_requires_testing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIFROST_ENVIRONMENT", "production")
    monkeypatch.setenv(
        "BIFROST_DATABASE_URL_SYNC",
        "postgresql://bifrost:secret@postgres/bifrost_test",
    )

    with pytest.raises(RuntimeError, match="BIFROST_ENVIRONMENT=testing"):
        gauntlet._require_safe_environment("bifrost-reliability-run")


def test_gauntlet_requires_test_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIFROST_ENVIRONMENT", "testing")
    monkeypatch.setenv(
        "BIFROST_DATABASE_URL_SYNC",
        "postgresql://bifrost:secret@postgres/bifrost",
    )

    with pytest.raises(RuntimeError, match=r"\*_test"):
        gauntlet._require_safe_environment("bifrost-reliability-run")


def test_gauntlet_requires_isolated_queue_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BIFROST_ENVIRONMENT", "testing")
    monkeypatch.setenv(
        "BIFROST_DATABASE_URL_SYNC",
        "postgresql://bifrost:secret@postgres/bifrost_test",
    )

    with pytest.raises(RuntimeError, match="namespace is not isolated"):
        gauntlet._require_safe_environment("workflow-executions")


def test_gauntlet_has_no_production_activation_surface() -> None:
    source = inspect.getsource(gauntlet)

    assert "BIFROST_EXECUTION_FAULT" not in source
    assert "workflow-executions-poison" not in source
    assert "replay_message(" not in source
    assert "discard_message(" not in source
    assert gauntlet.REPORT_PATH == Path(
        "/bifrost-results/execution-reliability-gauntlet.json"
    )


def test_gauntlet_declares_all_high_risk_scenarios() -> None:
    source = inspect.getsource(gauntlet.run)

    assert "_duplicate_delivery" in source
    assert "_retry_publication_interruption" in source
    assert "_worker_death_after_claim" in source
    assert "_admission_saturation" in source
    assert "_graceful_shutdown" in source
