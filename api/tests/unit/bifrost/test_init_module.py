from __future__ import annotations

from unittest.mock import Mock

import pytest

import bifrost


def test_compute_version_prefers_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIFROST_VERSION", "2026.7.5")
    check_output = Mock()
    monkeypatch.setattr(bifrost._subprocess, "check_output", check_output)

    assert bifrost._compute_version() == "2026.7.5"
    check_output.assert_not_called()


def test_compute_version_uses_git_describe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BIFROST_VERSION", raising=False)
    check_output = Mock(return_value="v1.2.3-dirty\n")
    monkeypatch.setattr(bifrost._subprocess, "check_output", check_output)

    assert bifrost._compute_version() == "v1.2.3-dirty"
    check_output.assert_called_once_with(
        ["git", "describe", "--tags", "--always", "--dirty"],
        text=True,
        stderr=bifrost._subprocess.DEVNULL,
    )


def test_compute_version_falls_back_to_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BIFROST_VERSION", raising=False)
    monkeypatch.setattr(
        bifrost._subprocess,
        "check_output",
        Mock(side_effect=OSError("git unavailable")),
    )

    assert bifrost._compute_version() == "unknown"


def test_unknown_lazy_attribute_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no attribute 'not_real'"):
        bifrost.__getattr__("not_real")


def test_public_exports_include_sdk_modules_models_decorators_and_errors() -> None:
    expected = {
        "agents",
        "api",
        "ai",
        "config",
        "ExecutionContext",
        "ExecutionStatus",
        "ConfigType",
        "FormFieldType",
        "UserError",
        "WorkflowError",
        "ConfigurationError",
        "workflow",
        "data_provider",
        "tool",
    }

    assert expected.issubset(set(bifrost.__all__))
    assert bifrost.ExecutionStatus.SUCCESS.value == "Success"
    assert bifrost.ConfigType.SECRET.value == "secret"
    assert bifrost.FormFieldType.MULTI_SELECT.value == "multi_select"
