"""Runtime-neutral execution core for the Cloudflare Python Worker."""

from __future__ import annotations

import contextlib
import contextvars
import builtins
import inspect
import io
import json
import time
import traceback
from types import ModuleType
from typing import Any


_execution_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "bifrost_cloudflare_execution_context", default={}
)


class _ContextProxy:
    def __getattr__(self, name: str) -> Any:
        value = _execution_context.get().get(name)
        if isinstance(value, dict):
            return _MappingProxy(value)
        return value


class _MappingProxy:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def __getattr__(self, name: str) -> Any:
        value = self._values.get(name)
        if isinstance(value, dict):
            return _MappingProxy(value)
        return value

    def __getitem__(self, name: str) -> Any:
        return self._values[name]

    def get(self, name: str, default: Any = None) -> Any:
        return self._values.get(name, default)


def _decorator(_func=None, **_kwargs):
    """Preserve Bifrost decorator syntax without container-only discovery."""
    def wrap(func):
        return func

    return wrap(_func) if _func is not None else wrap


def _bifrost_compatibility_module() -> ModuleType:
    module = ModuleType("bifrost")
    module.workflow = _decorator
    module.tool = _decorator
    module.data_provider = _decorator
    module.context = _ContextProxy()
    return module


_BIFROST_MODULE = _bifrost_compatibility_module()


def _execution_builtins() -> dict[str, Any]:
    """Return request-local builtins that intercept only Bifrost SDK imports."""
    values = vars(builtins).copy()
    native_import = builtins.__import__

    def compatibility_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and (name == "bifrost" or name.startswith("bifrost.")):
            return _BIFROST_MODULE
        return native_import(name, globals, locals, fromlist, level)

    values["__import__"] = compatibility_import
    return values


def _json_safe(value: Any) -> Any:
    """Require the same JSON transport boundary used by Bifrost execution rows."""
    encoded = json.dumps(value, default=str)
    return json.loads(encoded)


async def execute_payload(payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    execution_id = str(payload.get("execution_id") or "")
    context = payload.get("context") or {}
    token = _execution_context.set(context)
    stdout = io.StringIO()
    try:
        if payload.get("version") != 1:
            raise ValueError("unsupported Bifrost executor protocol version")
        source = payload.get("source")
        function_name = payload.get("function_name")
        parameters = payload.get("parameters") or {}
        if not execution_id or not isinstance(source, str) or not function_name:
            raise ValueError("execution_id, source, and function_name are required")
        if not isinstance(parameters, dict):
            raise ValueError("parameters must be an object")

        namespace: dict[str, Any] = {
            "__name__": f"bifrost_remote_{execution_id.replace('-', '_')}",
            "__file__": payload.get("file_path") or "<bifrost-cloudflare-workflow>",
            "__builtins__": _execution_builtins(),
        }
        with contextlib.redirect_stdout(stdout):
            exec(compile(source, namespace["__file__"], "exec"), namespace)
            function = namespace.get(str(function_name))
            if not callable(function):
                raise LookupError(f"workflow function '{function_name}' was not found")
            result = function(**parameters)
            if inspect.isawaitable(result):
                result = await result

        return {
            "execution_id": execution_id,
            "success": True,
            "status": "Success",
            "result": _json_safe(result),
            "variables": {},
            "execution_context": _json_safe(context),
            "roi": _json_safe(context.get("roi") or {}),
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "logs": stdout.getvalue().splitlines(),
        }
    except Exception as exc:
        return {
            "execution_id": execution_id,
            "success": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "traceback": traceback.format_exc(limit=12),
            "logs": stdout.getvalue().splitlines(),
        }
    finally:
        _execution_context.reset(token)
