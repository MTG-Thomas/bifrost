"""Auto-negotiating Streamable HTTP MCP client, per connection.

Exactly one transport: FastMCP's supported HTTP client. It probes the modern
``server/discover`` path first and falls back to the legacy ``initialize``
handshake only when the peer does not provide positive modern-era evidence.
No SSE or stdio transports are exposed here.

The caller — ``dispatch.invoke`` and ``catalog_sync.sync_catalog`` — owns
auth resolution. By the time we get the ``access_token`` here, the caller
has already decided whether the vendor will see the user identity or the
service identity. This module only carries bytes over a wire.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from src.models.orm.external_mcp import MCPConnection

if TYPE_CHECKING:
    from fastmcp import Client

logger = logging.getLogger(__name__)


def _resolve_server_url(connection: MCPConnection) -> str:
    """Pick the URL to dial for a connection.

    Per-org URL overrides (``server_url_override``) take precedence over
    the template's default URL. Most orgs leave the override blank.
    """
    return connection.server_url_override or connection.server.server_url


@asynccontextmanager
async def open_client(
    connection: MCPConnection,
    access_token: str,
) -> AsyncIterator[Client]:
    """Open an auto-negotiated Streamable HTTP MCP client.

    Entering FastMCP's client context performs modern-first negotiation. The
    yielded client exposes raw protocol methods such as ``list_tools_mcp`` and
    ``call_tool_mcp``; callers use those so Bifrost preserves the SDK's
    ``CallToolResult`` rather than FastMCP's convenience result wrapper.

    Args:
        connection: The ``MCPConnection`` row whose server URL (or override)
            we dial. The connection's ``server`` relationship must already
            be loaded — callers fetch it with ``joinedload`` or rely on the
            ORM's eager-load default.
        access_token: The Bearer token to send. Resolution of which token
            to use (per-user vs. shared service) happens in ``auth_resolution``;
            this layer is auth-agnostic.
    """
    # Imported lazily so the MCP SDK and its HTTP dependencies
    # stays out of the worker import closure (tests/unit/test_import_hygiene.py).
    from fastmcp import Client

    server_url = _resolve_server_url(connection)
    logger.debug(
        "Opening MCP client: connection=%s url=%s negotiation=auto",
        connection.id,
        server_url,
    )

    client = Client(server_url, auth=access_token, mode="auto")
    async with client:
        negotiation_path = (
            "modern_discover"
            if client.initialize_result is None
            else "legacy_initialize"
        )
        logger.info(
            "MCP client negotiated: connection=%s protocol_version=%s path=%s",
            connection.id,
            client.protocol_version,
            negotiation_path,
        )
        yield client
