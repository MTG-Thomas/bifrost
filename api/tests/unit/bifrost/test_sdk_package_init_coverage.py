import subprocess
import sys
import types
import importlib
import importlib.util
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
    ai_module = types.ModuleType("bifrost.ai")
    ai_module.ai = sentinel
    original_package_ai = vars(bifrost).get("ai")
    had_package_ai = "ai" in vars(bifrost)
    original_ai_module = sys.modules.get("bifrost.ai")

    try:
        if had_package_ai:
            delattr(bifrost, "ai")
        monkeypatch.setitem(sys.modules, "bifrost.ai", ai_module)

        assert bifrost.__getattr__("ai") is sentinel
        assert bifrost.ai is sentinel
    finally:
        if "ai" in vars(bifrost):
            delattr(bifrost, "ai")
        if had_package_ai:
            bifrost.ai = original_package_ai
        if original_ai_module is not None:
            sys.modules["bifrost.ai"] = original_ai_module


def test_getattr_rejects_unknown_export() -> None:
    with pytest.raises(AttributeError, match="no attribute 'missing'"):
        bifrost.__getattr__("missing")


def test_all_exports_core_sdk_symbols() -> None:
    assert {"workflow", "data_provider", "tool", "ExecutionContext"}.issubset(
        set(bifrost.__all__)
    )


def test_standalone_sdk_fallbacks_define_decorators_errors_and_enums(monkeypatch) -> None:
    original_decorators = sys.modules.pop("src.sdk.decorators", None)
    original_errors = sys.modules.pop("src.sdk.errors", None)
    original_spec_from_file_location = importlib.util.spec_from_file_location
    original_import = __import__

    def guarded_import(name, *args, **kwargs):
        if name in {"src.sdk.decorators", "src.sdk.errors"}:
            raise ImportError(name)
        return original_import(name, *args, **kwargs)

    def no_enum_spec(name, location, *args, **kwargs):
        if str(location).endswith("src/models/enums.py"):
            return None
        return original_spec_from_file_location(name, location, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)
    monkeypatch.setattr(importlib.util, "spec_from_file_location", no_enum_spec)

    try:
        reloaded = importlib.reload(bifrost)

        assert reloaded.workflow(lambda: None).__bifrost_metadata__.type == "workflow"
        assert reloaded.UserError("bad").args == ("bad",)
        assert reloaded.WorkflowError("failed").args == ("failed",)
        assert reloaded.ValidationError("invalid").args == ("invalid",)
        assert reloaded.IntegrationError("integration").args == ("integration",)
        assert reloaded.ConfigurationError("config").args == ("config",)
        assert reloaded.ExecutionStatus.CANCELLED.value == "Cancelled"
        assert reloaded.ConfigType.SECRET.value == "secret"
        assert reloaded.FormFieldType.FILE.value == "file"
    finally:
        if original_decorators is not None:
            sys.modules["src.sdk.decorators"] = original_decorators
        if original_errors is not None:
            sys.modules["src.sdk.errors"] = original_errors
        importlib.reload(bifrost)
