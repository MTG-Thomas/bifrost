import subprocess
import sys
from types import SimpleNamespace

import pytest

import bifrost


pytestmark = pytest.mark.unit


def test_compute_version_prefers_environment(monkeypatch) -> None:
    monkeypatch.setenv("BIFROST_VERSION", "2026.7.test")

    assert bifrost._compute_version() == "2026.7.test"


def test_compute_version_uses_git_describe_when_environment_absent(monkeypatch) -> None:
    monkeypatch.delenv("BIFROST_VERSION", raising=False)
    monkeypatch.setattr(
        bifrost._subprocess,
        "check_output",
        lambda *args, **kwargs: "v1.2.3-dirty\n",
    )

    assert bifrost._compute_version() == "v1.2.3-dirty"


def test_compute_version_falls_back_to_unknown_when_git_fails(monkeypatch) -> None:
    monkeypatch.delenv("BIFROST_VERSION", raising=False)

    def fail_git(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["git"])

    monkeypatch.setattr(bifrost._subprocess, "check_output", fail_git)

    assert bifrost._compute_version() == "unknown"


def test_getattr_lazy_loads_ai_once(monkeypatch) -> None:
    sentinel = SimpleNamespace(name="ai-module")
    ai_module = SimpleNamespace(ai=sentinel)
    monkeypatch.delattr(bifrost, "ai", raising=False)
    monkeypatch.setitem(sys.modules, "bifrost.ai", ai_module)

    assert bifrost.__getattr__("ai") is sentinel
    assert bifrost.ai is sentinel


def test_getattr_rejects_unknown_export() -> None:
    with pytest.raises(AttributeError, match="no attribute 'missing'"):
        bifrost.__getattr__("missing")


def test_all_exports_core_sdk_symbols() -> None:
    assert {"workflow", "data_provider", "tool", "ExecutionContext"}.issubset(
        set(bifrost.__all__)
    )
