"""MCP Tasks adapter over Bifrost's existing durable operation lifecycles."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, Literal, cast

from fastmcp.server.extensions import (
    MethodBinding,
    ServerExtension,
    read_client_extension_settings,
)
from fastmcp.utilities.tasks import TASKS_EXTENSION_ID
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, RequestParams
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from pydantic import BaseModel, ConfigDict, Field

from src.services.mcp_server.tools._http_bridge import call_rest

_TASK_REQUESTED: ContextVar[bool] = ContextVar(
    "bifrost_mcp_task_requested",
    default=False,
)
_TASK_METHOD_VERSIONS = frozenset(MODERN_PROTOCOL_VERSIONS)
_STOCK_RESULT_SURFACE: Any = object()
_original_result_serializer: Any = None
_serializer_holds = 0

TaskStatus = Literal["working", "completed", "failed", "cancelled"]
TaskKind = Literal["platform-job", "execution", "agent-run"]


class TaskParams(RequestParams):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")


class TaskFields(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(serialization_alias="taskId")
    status: TaskStatus
    created_at: str = Field(serialization_alias="createdAt")
    last_updated_at: str = Field(serialization_alias="lastUpdatedAt")
    ttl_ms: float | None = Field(serialization_alias="ttlMs")
    status_message: str | None = Field(
        default=None,
        serialization_alias="statusMessage",
    )
    poll_interval_ms: float | None = Field(
        default=1000,
        serialization_alias="pollIntervalMs",
    )


class TaskResult(TaskFields):
    result_type: Literal["complete"] = Field(
        default="complete",
        serialization_alias="resultType",
    )
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class CreateTaskResult(TaskFields):
    result_type: Literal["task"] = Field(
        default="task",
        serialization_alias="resultType",
    )


class CancelTaskResult(BaseModel):
    result_type: Literal["complete"] = Field(
        default="complete",
        serialization_alias="resultType",
    )


def is_mcp_task_requested() -> bool:
    """Return whether the current gateway call negotiated MCP Tasks."""
    return _TASK_REQUESTED.get()


def encode_task_id(kind: TaskKind, durable_id: str) -> str:
    """Expose the existing durable handle without minting a task record."""
    return f"{kind}:{durable_id}"


def decode_task_id(task_id: str) -> tuple[TaskKind, str]:
    for kind in ("platform-job", "execution", "agent-run"):
        prefix = f"{kind}:"
        if task_id.startswith(prefix) and task_id.removeprefix(prefix):
            return kind, task_id.removeprefix(prefix)  # type: ignore[return-value]
    raise MCPError(code=INVALID_PARAMS, message="Task not found")


def _runtime_context():
    from src.services.mcp_server.server import _get_context_from_token

    return _get_context_from_token()


def _task_path(kind: TaskKind, durable_id: str) -> str:
    return {
        "platform-job": f"/api/platform-jobs/{durable_id}",
        "execution": f"/api/executions/{durable_id}",
        "agent-run": f"/api/agent-runs/{durable_id}",
    }[kind]


def _task_status(kind: TaskKind, body: dict[str, Any]) -> TaskStatus:
    raw = str(body.get("status", "")).lower()
    if kind == "execution":
        return cast(TaskStatus, {
            "success": "completed",
            "failed": "failed",
            "timeout": "failed",
            "stuck": "failed",
            "completedwitherrors": "failed",
            "cancelled": "cancelled",
        }.get(raw, "working"))
    if kind == "agent-run":
        return cast(TaskStatus, {
            "completed": "completed",
            "failed": "failed",
            "timeout": "failed",
            "budget_exceeded": "failed",
            "paused": "failed",
            "cancelled": "cancelled",
        }.get(raw, "working"))
    return cast(TaskStatus, {
        "succeeded": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(raw, "working"))


def _task_result_payload(kind: TaskKind, body: dict[str, Any]) -> dict[str, Any] | None:
    if kind == "platform-job":
        return body.get("result")
    if kind == "execution":
        return {
            "execution_id": body.get("execution_id"),
            "result": body.get("result"),
            "result_type": body.get("result_type"),
        }
    return {
        "run_id": body.get("id"),
        "output": body.get("output"),
    }


def _task_error(kind: TaskKind, body: dict[str, Any]) -> dict[str, Any] | None:
    if kind == "platform-job":
        value = body.get("error")
        return value if isinstance(value, dict) else None
    value = body.get("error_message") if kind == "execution" else body.get("error")
    return {"message": str(value)} if value else None


def _serialize_task_result(
    method: str,
    version: str,
    data: Mapping[str, Any],
    *,
    surface: Any = _STOCK_RESULT_SURFACE,
) -> dict[str, Any]:
    """Preserve the extension-claimed task result through MCP serialization."""
    if (
        surface is _STOCK_RESULT_SURFACE
        and method == "tools/call"
        and version in MODERN_PROTOCOL_VERSIONS
        and data.get("resultType") == "task"
    ):
        return dict(data)
    if surface is _STOCK_RESULT_SURFACE:
        return _original_result_serializer(method, version, data)
    return _original_result_serializer(method, version, data, surface=surface)


def _install_task_result_serializer() -> None:
    """Install FastMCP's current producer-side task-result compatibility hook."""
    global _original_result_serializer, _serializer_holds

    import mcp_types.methods as methods

    _serializer_holds += 1
    if _original_result_serializer is None:
        _original_result_serializer = methods.serialize_server_result
        methods.serialize_server_result = _serialize_task_result


def _uninstall_task_result_serializer() -> None:
    global _original_result_serializer, _serializer_holds

    import mcp_types.methods as methods

    _serializer_holds = max(0, _serializer_holds - 1)
    if _serializer_holds == 0 and _original_result_serializer is not None:
        methods.serialize_server_result = _original_result_serializer
        _original_result_serializer = None


async def get_task_result(task_id: str) -> TaskResult:
    """Read a canonical lifecycle through its requester-authorized REST route."""
    kind, durable_id = decode_task_id(task_id)
    status_code, body = await call_rest(
        _runtime_context(),
        "GET",
        _task_path(kind, durable_id),
    )
    if status_code != 200 or not isinstance(body, dict):
        raise MCPError(code=INVALID_PARAMS, message="Task not found")

    status = _task_status(kind, body)
    created_at = str(
        body.get("created_at")
        or body.get("started_at")
        or body.get("completed_at")
        or "1970-01-01T00:00:00+00:00"
    )
    updated_at = str(body.get("updated_at") or body.get("completed_at") or created_at)
    progress: dict[str, Any] = (
        body["progress"] if isinstance(body.get("progress"), dict) else {}
    )
    message = progress.get("phase") or body.get("status")
    return TaskResult(
        task_id=task_id,
        status=status,
        created_at=created_at,
        last_updated_at=updated_at,
        ttl_ms=None,
        status_message=str(message) if message else None,
        result=_task_result_payload(kind, body) if status == "completed" else None,
        error=_task_error(kind, body) if status == "failed" else None,
    )


class BifrostTasksExtension(ServerExtension):
    """SEP-2663 wire adapter with no independent task backend or worker."""

    identifier = TASKS_EXTENSION_ID

    def settings(self) -> dict[str, Any]:
        return {}

    def methods(self):
        return (
            MethodBinding(
                method="tasks/get",
                params_type=TaskParams,
                handler=self._handle_get,
                protocol_versions=_TASK_METHOD_VERSIONS,
            ),
            MethodBinding(
                method="tasks/cancel",
                params_type=TaskParams,
                handler=self._handle_cancel,
                protocol_versions=_TASK_METHOD_VERSIONS,
            ),
        )

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        # FastMCP 4.0.0b1 validates tools/call against the core result union,
        # which strips extension-claimed task fields. Its official tasks
        # extension installs the same producer-side serializer hook. Bifrost
        # owns only the wire adapter here; no task backend or worker is started.
        _install_task_result_serializer()
        try:
            yield
        finally:
            _uninstall_task_result_serializer()

    @staticmethod
    def _require_capability(ctx: ServerRequestContext[Any, Any]) -> None:
        if read_client_extension_settings(ctx, TASKS_EXTENSION_ID) is None:
            raise MCPError(
                code=INVALID_PARAMS,
                message="The client did not negotiate the MCP Tasks extension",
            )

    async def _handle_get(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: TaskParams,
    ) -> TaskResult:
        self._require_capability(ctx)
        return await get_task_result(params.task_id)

    async def _handle_cancel(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: TaskParams,
    ) -> CancelTaskResult:
        self._require_capability(ctx)
        kind, durable_id = decode_task_id(params.task_id)
        status_code, _body = await call_rest(
            _runtime_context(),
            "POST",
            f"{_task_path(kind, durable_id)}/cancel",
        )
        if status_code not in (200, 202):
            raise MCPError(code=INVALID_PARAMS, message="Task not found or not cancellable")
        return CancelTaskResult()

    async def intercept_tool_call(self, params, context, call_next):
        if params.name != "bifrost_execute_tool":
            return await call_next()
        request_context = context.request_context
        opted_in = (
            request_context is not None
            and request_context.protocol_version in MODERN_PROTOCOL_VERSIONS
            and context.client_extension_settings(TASKS_EXTENSION_ID) is not None
        )
        if not opted_in:
            return await call_next()

        token = _TASK_REQUESTED.set(True)
        try:
            outcome = await call_next()
        finally:
            _TASK_REQUESTED.reset(token)

        structured = getattr(outcome, "structured_content", None)
        handle = structured.get("durable_handle") if isinstance(structured, dict) else None
        if not isinstance(handle, dict) or not handle.get("kind") or not handle.get("id"):
            return outcome
        task_id = encode_task_id(handle["kind"], str(handle["id"]))
        current = await get_task_result(task_id)
        return CreateTaskResult(
            task_id=task_id,
            status=current.status,
            created_at=current.created_at,
            last_updated_at=current.last_updated_at,
            ttl_ms=None,
            status_message=current.status_message,
        )
