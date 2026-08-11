from __future__ import annotations

import subprocess
import sys
import types
from types import SimpleNamespace

import pytest

sys.modules.setdefault(
    "resource",
    types.SimpleNamespace(
        RUSAGE_SELF=0,
        getrusage=lambda _who: SimpleNamespace(
            ru_maxrss=0,
            ru_utime=0,
            ru_stime=0,
        ),
    ),
)

from src.services.execution import simple_worker  # noqa: E402


def test_parse_requirements_splits_packages_and_options():
    content = """
    # ignored
    --index-url https://packages.example/simple
    -r shared.txt
    requests==2.32.0

    numpy>=2
    """

    packages, options = simple_worker._parse_requirements(content)

    assert packages == ["requests==2.32.0", "numpy>=2"]
    assert options == [
        "--index-url",
        "https://packages.example/simple",
        "-r",
        "shared.txt",
    ]
    assert simple_worker._parse_requirement_lines(content) == packages


def test_install_requirements_batch_success(monkeypatch):
    monkeypatch.setattr(
        "src.core.requirements_cache.get_requirements_sync",
        lambda: "requests==2.32.0\nnumpy>=2\n",
    )
    calls: list[list[str]] = []

    def fake_pip_install(args: list[str]):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(simple_worker, "_pip_install", fake_pip_install)

    result = simple_worker.install_requirements()

    assert result.ok is True
    assert result.attempted == ["requests==2.32.0", "numpy>=2"]
    assert result.installed == ["requests==2.32.0", "numpy>=2"]
    assert result.failed == []
    assert len(calls) == 1
    assert calls[0][0] == "-r"


def test_install_requirements_falls_back_and_records_per_package_failures(monkeypatch):
    monkeypatch.setattr(
        "src.core.requirements_cache.get_requirements_sync",
        lambda: "--extra-index-url https://packages.example/simple\nokpkg==1\nbadpkg==2\nslowpkg==3\n",
    )
    calls: list[list[str]] = []

    def fake_pip_install(args: list[str]):
        calls.append(args)
        if args[0] == "-r":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="batch failed")
        if args[-1] == "okpkg==1":
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[-1] == "badpkg==2":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="bad build")
        raise subprocess.TimeoutExpired(args, 300)

    monkeypatch.setattr(simple_worker, "_pip_install", fake_pip_install)

    result = simple_worker.install_requirements()

    assert result.ok is False
    assert result.attempted == ["okpkg==1", "badpkg==2", "slowpkg==3"]
    assert result.installed == ["okpkg==1"]
    assert [(failure.package, failure.error) for failure in result.failed] == [
        ("badpkg==2", "bad build"),
        ("slowpkg==3", "pip install timed out (300s)"),
    ]
    assert calls[1] == [
        "--extra-index-url",
        "https://packages.example/simple",
        "okpkg==1",
    ]


@pytest.mark.asyncio
async def test_execute_async_missing_context_shapes_context_not_found(monkeypatch):
    async def missing_context(_execution_id):
        return None

    monkeypatch.setattr(simple_worker, "_read_context_from_redis", missing_context)

    result = await simple_worker._execute_async("exec-missing", "worker-1")

    assert result == {
        "execution_id": "exec-missing",
        "success": False,
        "error": "Execution context not found in Redis",
        "error_type": "ContextNotFound",
        "duration_ms": 0,
        "worker_id": "worker-1",
    }


@pytest.mark.asyncio
async def test_execute_async_success_shapes_engine_result_and_pss_delta(monkeypatch):
    context = {"solution_id": "solution-1", "solution_global_repo_access": True}
    calls: list[tuple[str, object]] = []
    pss_values = iter([1000, 2500])

    async def read_context(_execution_id):
        return context

    monkeypatch.setattr(simple_worker, "_read_context_from_redis", read_context)
    monkeypatch.setattr(
        "src.core.module_cache_sync.set_solution_context",
        lambda solution_id, *, global_repo_access: calls.append(
            ("solution", (solution_id, global_repo_access))
        ),
    )
    def clear_modules():
        calls.append(("clear", None))
        return simple_worker.WorkspaceModuleRefresh(
            generation="generation-1",
            cleared=2,
            kept=3,
            generation_mismatch=True,
        )

    monkeypatch.setattr(simple_worker, "_clear_workspace_modules", clear_modules)
    monkeypatch.setattr(simple_worker, "_get_pss_bytes", lambda: next(pss_values))

    async def fake_run_execution(_execution_id, _context):
        return {
            "status": "Success",
            "result": {"ok": True},
            "duration_ms": 17,
            "logs": [{"message": "done"}],
            "variables": {"x": 1},
            "integration_calls": [{"name": "tool"}],
            "roi": {"minutes_saved": 3},
            "metrics": {"peak_memory_bytes": 999},
            "cached": True,
            "cache_expires_at": "2026-07-05T00:00:00Z",
            "execution_context": {"execution_id": "exec-1"},
        }

    monkeypatch.setattr(
        "src.services.execution.worker._run_execution",
        fake_run_execution,
    )

    result = await simple_worker._execute_async("exec-1", "worker-1")

    assert result["success"] is True
    assert result["status"] == "Success"
    assert result["result"] == {"ok": True}
    assert result["metrics"]["peak_memory_bytes"] == 1500
    assert result["metrics"]["workspace_modules_cleared"] == 2
    assert result["metrics"]["workspace_generation_mismatch"] is True
    assert result["execution_context"]["workspace_generation"] == "generation-1"
    assert result["cached"] is True
    assert result["worker_id"] == "worker-1"
    assert calls == [("solution", ("solution-1", True)), ("clear", None)]


@pytest.mark.asyncio
async def test_execute_async_engine_exception_shapes_failure(monkeypatch):
    async def read_context(_execution_id):
        return {}

    monkeypatch.setattr(simple_worker, "_read_context_from_redis", read_context)
    monkeypatch.setattr(
        simple_worker,
        "_clear_workspace_modules",
        lambda: simple_worker.WorkspaceModuleRefresh(
            generation="generation-1",
            cleared=0,
            kept=0,
            generation_mismatch=False,
        ),
    )
    monkeypatch.setattr(simple_worker, "_get_pss_bytes", lambda: 0)

    async def fake_run_execution(_execution_id, _context):
        raise RuntimeError("engine boom")

    monkeypatch.setattr(
        "src.services.execution.worker._run_execution",
        fake_run_execution,
    )

    result = await simple_worker._execute_async("exec-1", "worker-1")

    assert result["execution_id"] == "exec-1"
    assert result["success"] is False
    assert result["error"] == "engine boom"
    assert result["error_type"] == "RuntimeError"
    assert result["worker_id"] == "worker-1"
    assert result["duration_ms"] >= 0


def test_capture_resource_metrics_converts_linux_ru_maxrss(monkeypatch):
    monkeypatch.setattr(simple_worker.sys, "platform", "linux")
    monkeypatch.setattr(
        simple_worker.resource,
        "getrusage",
        lambda _who: SimpleNamespace(ru_maxrss=12, ru_utime=1.23456, ru_stime=0.5),
    )

    assert simple_worker._capture_resource_metrics() == {
        "peak_memory_bytes": 12288,
        "cpu_user_seconds": 1.2346,
        "cpu_system_seconds": 0.5,
        "cpu_total_seconds": 1.7346,
    }


def test_clear_workspace_modules_purges_entire_closure_on_generation_change(
    monkeypatch,
):
    from src.core import module_cache_sync
    from src.services.execution.virtual_import import VirtualModuleLoader

    module_name = "workspace_generation_probe"
    module = types.ModuleType(module_name)
    module.__file__ = f"{module_name}.py"
    module.__content_hash__ = "same-hash"
    module.__workspace_generation__ = "generation-1"
    module.__loader__ = VirtualModuleLoader.__new__(VirtualModuleLoader)
    sys.modules[module_name] = module
    monkeypatch.setattr(
        module_cache_sync,
        "wait_for_workspace_generation_sync",
        lambda: "generation-2",
    )
    monkeypatch.setattr(
        module_cache_sync, "get_module_index_sync", lambda: [f"{module_name}.py"]
    )
    monkeypatch.setattr(
        module_cache_sync,
        "get_modules_sync",
        lambda paths: {path: {"hash": "same-hash"} for path in paths},
    )

    try:
        refresh = simple_worker._clear_workspace_modules()
    finally:
        sys.modules.pop(module_name, None)

    assert refresh == simple_worker.WorkspaceModuleRefresh(
        generation="generation-2",
        cleared=1,
        kept=0,
        generation_mismatch=True,
    )
    assert module_name not in sys.modules
