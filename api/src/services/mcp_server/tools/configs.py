"""Config MCP Tools — thin wrappers around the REST API.

Implements Task 6 of the CLI mutation surface + MCP parity plan:
``list_configs``, ``create_config``, ``update_config``, ``delete_config``.

Same rules as :mod:`roles`: validate minimal inputs, resolve refs, then
call the REST endpoint via the in-process HTTP bridge. No ORM, no
repositories, no ``AsyncSession``.

DTO-driven: parameters mirror :class:`ConfigCreate` / :class:`ConfigUpdate`
with the ``config_type`` → ``type`` wire alias applied by
:func:`bifrost.dto_flags.assemble_body`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.tools.tool import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest, rest_client

logger = logging.getLogger(__name__)


def _ref_error_payload(exc: Exception) -> dict[str, Any]:
    from bifrost.refs import AmbiguousRefError, RefNotFoundError

    if isinstance(exc, AmbiguousRefError):
        return {"kind": exc.kind, "value": exc.value, "candidates": exc.candidates}
    if isinstance(exc, RefNotFoundError):
        return {"kind": exc.kind, "value": exc.value}
    return {"detail": str(exc)}


async def _assemble_config_body(
    context: Any, fields: dict[str, Any], *, is_update: bool
) -> dict[str, Any]:
    from bifrost.dto_flags import DTO_EXCLUDES, assemble_body
    from bifrost.refs import RefResolver
    from src.models.contracts.config import ConfigCreate, ConfigUpdate

    model_cls = ConfigUpdate if is_update else ConfigCreate
    exclude = DTO_EXCLUDES.get(model_cls.__name__, set())

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        return await assemble_body(
            model_cls,
            {k: v for k, v in fields.items() if k not in exclude},
            resolver=resolver,
        )


async def list_configs(context: Any) -> ToolResult:
    """List configs visible to the caller — ``GET /api/config``."""
    logger.info("MCP list_configs (HTTP bridge)")
    status_code, body = await call_rest(context, "GET", "/api/config")
    if status_code != 200:
        return error_result(f"list_configs failed: HTTP {status_code}", {"body": body})
    items = body if isinstance(body, list) else []
    return success_result(
        f"Found {len(items)} config(s)",
        {"configs": items, "count": len(items)},
    )


async def create_config(
    context: Any,
    key: str,
    value: dict[str, Any],
    config_type: str | None = None,
    description: str | None = None,
    organization_id: str | None = None,
) -> ToolResult:
    """Create a config — ``POST /api/config``.

    ``value`` is a dict per the :class:`ConfigCreate` contract. ``config_type``
    accepts the :class:`ConfigType` enum values (``string``, ``integer``,
    ``boolean``, ``json``, ``secret``). ``organization_id`` is a ref (UUID,
    name) or ``None`` for global scope — resolved via :class:`RefResolver`.
    """
    if not key:
        return error_result("key is required")

    fields = {
        "key": key,
        "value": value,
        "config_type": config_type,
        "description": description,
        "organization_id": organization_id,
    }
    try:
        body = await _assemble_config_body(context, fields, is_update=False)
    except Exception as exc:
        return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(context, "POST", "/api/config", json_body=body)
    if status_code not in (200, 201):
        return error_result(f"create_config failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Created config: {key}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def update_config(
    context: Any,
    config_ref: str,
    value: dict[str, Any] | None = None,
    config_type: str | None = None,
    description: str | None = None,
) -> ToolResult:
    """Update a config — ``PUT /api/config/{uuid}``.

    ``config_ref`` is a UUID or config ``key``. Omitting ``value`` preserves
    the stored value (the server honours unset-means-omit; particularly
    important for secret-type configs).
    """
    if not config_ref:
        return error_result("config_ref is required")

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            config_uuid = await resolver.resolve("config", config_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve config {config_ref!r}",
                _ref_error_payload(exc),
            )

    fields = {
        "value": value,
        "config_type": config_type,
        "description": description,
    }
    try:
        body = await _assemble_config_body(context, fields, is_update=True)
    except Exception as exc:
        return error_result(f"invalid input: {exc}", _ref_error_payload(exc))

    status_code, resp = await call_rest(
        context, "PUT", f"/api/config/{config_uuid}", json_body=body
    )
    if status_code != 200:
        return error_result(f"update_config failed: HTTP {status_code}", {"body": resp})
    return success_result(
        f"Updated config {config_uuid}",
        resp if isinstance(resp, dict) else {"body": resp},
    )


async def delete_config(context: Any, config_ref: str) -> ToolResult:
    """Delete a config — ``DELETE /api/config/{uuid}``.

    ``config_ref`` is a UUID or config key. No ``--confirm`` guard here:
    the MCP surface returns REST errors straight through; the CLI layers
    a secret-aware confirm prompt on top.
    """
    if not config_ref:
        return error_result("config_ref is required")

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        resolver = RefResolver(http)
        try:
            config_uuid = await resolver.resolve("config", config_ref)
        except Exception as exc:
            return error_result(
                f"could not resolve config {config_ref!r}",
                _ref_error_payload(exc),
            )

    status_code, resp = await call_rest(
        context, "DELETE", f"/api/config/{config_uuid}"
    )
    if status_code not in (200, 204):
        return error_result(f"delete_config failed: HTTP {status_code}", {"body": resp})
    return success_result(f"Deleted config {config_uuid}", {"deleted": config_uuid})


TOOLS = [
    ("list_configs", "List Configs", "List configuration values for the caller's scope."),
    ("create_config", "Create Config", "Create a configuration value."),
    ("update_config", "Update Config", "Update a configuration value by UUID or key."),
    ("delete_config", "Delete Config", "Delete a configuration value by UUID or key."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all configs parity tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_funcs = {
        "list_configs": list_configs,
        "create_config": create_config,
        "update_config": update_config,
        "delete_config": delete_config,
    }

    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp, tool_funcs[tool_id], tool_id, description, get_context_fn
        )


__all__ = [
    "TOOLS",
    "create_config",
    "delete_config",
    "list_configs",
    "register_tools",
    "update_config",
]
