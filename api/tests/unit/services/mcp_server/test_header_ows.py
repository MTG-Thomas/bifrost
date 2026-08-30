"""Tests for MCP routing-header optional-whitespace parsing."""

from __future__ import annotations

import asyncio

from src.services.mcp_server.agent_scope import MCPHeaderOWSMiddleware


def test_mcp_header_ows_is_parsed_without_changing_other_header_values() -> None:
    observed_scope: dict | None = None

    async def app(scope, receive, send):
        nonlocal observed_scope
        observed_scope = scope

    middleware = MCPHeaderOWSMiddleware(app)
    original_scope = {
        "type": "http",
        "headers": [
            (b"mcp-method", b"\ttools/call "),
            (b"Mcp-Name", b"  bifrost_search_capabilities  "),
            (b"mcp-protocol-version", b" 2026-07-28\t"),
            (b"x-preserved", b"  exact  "),
        ],
    }

    async def invoke() -> None:
        async def receive():
            return {"type": "http.disconnect"}

        async def send(_):
            return None

        await middleware(original_scope, receive, send)

    asyncio.run(invoke())

    assert observed_scope is not None
    assert observed_scope["headers"] == [
        (b"mcp-method", b"tools/call"),
        (b"Mcp-Name", b"bifrost_search_capabilities"),
        (b"mcp-protocol-version", b"2026-07-28"),
        (b"x-preserved", b"  exact  "),
    ]
    assert original_scope["headers"][1][1] == b"  bifrost_search_capabilities  "
