"""Tests for the isolated MCP conformance authentication adapter."""

from __future__ import annotations

import asyncio

import jwt

from tests.conformance.auth_adapter import (
    MCP_RESOURCE,
    _authorization_header,
    _handle_client,
)


def test_adapter_mints_a_resource_bound_mcp_token() -> None:
    scheme, token = _authorization_header().split(" ", 1)
    claims = jwt.decode(token, options={"verify_signature": False})

    assert scheme == "Bearer"
    assert claims["aud"] == MCP_RESOURCE
    assert claims["resource"] == MCP_RESOURCE
    assert claims["scope"] == "mcp:access"
    assert claims["mcp"] is True


def test_adapter_preserves_raw_mcp_header_ows_through_upstream() -> None:
    async def exercise() -> tuple[bytes, bytes]:
        captured = bytearray()

        async def upstream(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            captured.extend(await reader.readuntil(b"\r\n\r\n"))
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 2\r\n"
                b"Connection: close\r\n\r\n{}"
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        adapter_server = await asyncio.start_server(
            lambda reader, writer: _handle_client(
                reader,
                writer,
                authorization="Bearer resource-bound-token",
                upstream_host="127.0.0.1",
                upstream_port=upstream_port,
            ),
            "127.0.0.1",
            0,
        )
        adapter_port = adapter_server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", adapter_port)
            writer.write(
                b"POST /mcp HTTP/1.1\r\n"
                b"Host: adapter:8080\r\n"
                b"Authorization: Bearer runner-value\r\n"
                b"MCP-Protocol-Version: 2026-07-28\r\n"
                b"Mcp-Method: tools/call\r\n"
                b"Mcp-Name:   bifrost_find_agents  \r\n"
                b"Content-Length: 2\r\n"
                b"Connection: keep-alive\r\n\r\n{}"
            )
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
        finally:
            adapter_server.close()
            upstream_server.close()
            await adapter_server.wait_closed()
            await upstream_server.wait_closed()

        return bytes(captured), response

    upstream_request, downstream_response = asyncio.run(exercise())

    assert b"Mcp-Name:   bifrost_find_agents  \r\n" in upstream_request
    assert b"Authorization: Bearer resource-bound-token\r\n" in upstream_request
    assert b"Host: 127.0.0.1:" in upstream_request
    assert b"Authorization: Bearer runner-value" not in upstream_request
    assert b"Connection: keep-alive" not in upstream_request
    assert downstream_response.endswith(b"{}")


def test_adapter_restricts_forwarding_to_mcp_path() -> None:
    async def exercise() -> bytes:
        async def forbidden_upstream(
            _: asyncio.StreamReader, __: asyncio.StreamWriter
        ) -> None:
            raise AssertionError("non-MCP path reached upstream")

        upstream_server = await asyncio.start_server(
            forbidden_upstream, "127.0.0.1", 0
        )
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        adapter_server = await asyncio.start_server(
            lambda reader, writer: _handle_client(
                reader,
                writer,
                authorization="Bearer resource-bound-token",
                upstream_host="127.0.0.1",
                upstream_port=upstream_port,
            ),
            "127.0.0.1",
            0,
        )
        adapter_port = adapter_server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", adapter_port)
            writer.write(
                b"GET /api/users HTTP/1.1\r\nHost: adapter:8080\r\n\r\n"
            )
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
        finally:
            adapter_server.close()
            upstream_server.close()
            await adapter_server.wait_closed()
            await upstream_server.wait_closed()
        return response

    response = asyncio.run(exercise())
    assert response.startswith(b"HTTP/1.1 404 Not Found\r\n")
