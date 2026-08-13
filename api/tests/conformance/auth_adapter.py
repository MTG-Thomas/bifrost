"""Test-only authenticated raw HTTP adapter for the official MCP runner.

The upstream conformance CLI cannot attach an Authorization header to server
requests. This adapter runs only on the isolated Compose test network and
forwards request bytes to Bifrost's real ``/mcp`` endpoint while adding a
resource-bound bearer token and canonical ``Host`` header. Keeping this adapter
at the raw HTTP layer is intentional: the official header-validation scenario
must reach Bifrost without an ASGI or HTTP client normalizing MCP field values.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from tests.fixtures.auth import create_test_jwt


UPSTREAM_ORIGIN = "http://api:8000"
MCP_RESOURCE = f"{UPSTREAM_ORIGIN}/mcp"
UPSTREAM_HOST = "api"
UPSTREAM_PORT = 8000
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8080
MAX_BODY_BYTES = 16 * 1024 * 1024

_HOP_BY_HOP_HEADERS = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"proxy-connection",
    b"te",
    b"trailer",
    b"transfer-encoding",
    b"upgrade",
}
_REPLACED_HEADERS = _HOP_BY_HOP_HEADERS | {b"authorization", b"host"}


def _authorization_header() -> str:
    token = create_test_jwt(is_superuser=True, mcp_resource=MCP_RESOURCE)
    return f"Bearer {token}"


def _split_request_head(head: bytes) -> tuple[bytes, list[bytes]]:
    lines = head.removesuffix(b"\r\n\r\n").split(b"\r\n")
    if not lines or len(lines[0].split(b" ")) != 3:
        raise ValueError("invalid HTTP request line")
    if any(not line or b":" not in line for line in lines[1:]):
        raise ValueError("invalid HTTP request header")
    return lines[0], lines[1:]


def _header_name(line: bytes) -> bytes:
    return line.split(b":", 1)[0].strip().lower()


def _header_value(line: bytes) -> bytes:
    return line.split(b":", 1)[1].strip(b" \t")


def _content_length(headers: Sequence[bytes]) -> int:
    values = [
        _header_value(line)
        for line in headers
        if _header_name(line) == b"content-length"
    ]
    if not values:
        return 0
    if len(values) != 1:
        raise ValueError("multiple Content-Length headers")
    try:
        length = int(values[0])
    except ValueError as exc:
        raise ValueError("invalid Content-Length header") from exc
    if length < 0 or length > MAX_BODY_BYTES:
        raise ValueError("request body exceeds adapter limit")
    return length


def _request_target(request_line: bytes) -> bytes:
    return request_line.split(b" ", 2)[1].split(b"?", 1)[0]


def _build_upstream_request(
    request_line: bytes,
    headers: Sequence[bytes],
    body: bytes,
    authorization: str,
    *,
    upstream_host: str = UPSTREAM_HOST,
    upstream_port: int = UPSTREAM_PORT,
) -> bytes:
    """Build an upstream request without modifying end-to-end header lines."""
    forwarded = [
        line for line in headers if _header_name(line) not in _REPLACED_HEADERS
    ]
    forwarded.extend(
        [
            f"Authorization: {authorization}".encode("ascii"),
            f"Host: {upstream_host}:{upstream_port}".encode("ascii"),
            b"Connection: close",
        ]
    )
    return b"\r\n".join([request_line, *forwarded, b"", body])


async def _send_response(
    writer: asyncio.StreamWriter,
    status: bytes,
    body: bytes,
    *,
    content_type: bytes = b"text/plain; charset=utf-8",
) -> None:
    writer.write(
        b"\r\n".join(
            [
                b"HTTP/1.1 " + status,
                b"Content-Type: " + content_type,
                f"Content-Length: {len(body)}".encode("ascii"),
                b"Connection: close",
                b"",
                body,
            ]
        )
    )
    await writer.drain()


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    authorization: str,
    upstream_host: str = UPSTREAM_HOST,
    upstream_port: int = UPSTREAM_PORT,
) -> None:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
        request_line, headers = _split_request_head(head)
        target = _request_target(request_line)
        body = await reader.readexactly(_content_length(headers))

        if target == b"/health":
            await _send_response(
                writer,
                b"200 OK",
                b'{"status":"ok"}',
                content_type=b"application/json",
            )
            return
        if target != b"/mcp":
            await _send_response(writer, b"404 Not Found", b"Not Found")
            return

        upstream_reader, upstream_writer = await asyncio.open_connection(
            upstream_host, upstream_port
        )
        try:
            upstream_writer.write(
                _build_upstream_request(
                    request_line,
                    headers,
                    body,
                    authorization,
                    upstream_host=upstream_host,
                    upstream_port=upstream_port,
                )
            )
            await upstream_writer.drain()
            while chunk := await upstream_reader.read(64 * 1024):
                writer.write(chunk)
                await writer.drain()
        finally:
            upstream_writer.close()
            await upstream_writer.wait_closed()
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
        await _send_response(writer, b"400 Bad Request", b"Bad Request")
    except (ConnectionError, OSError):
        await _send_response(writer, b"502 Bad Gateway", b"Bad Gateway")
    finally:
        writer.close()
        await writer.wait_closed()


async def serve() -> None:
    authorization = _authorization_header()
    server = await asyncio.start_server(
        lambda reader, writer: _handle_client(
            reader, writer, authorization=authorization
        ),
        LISTEN_HOST,
        LISTEN_PORT,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(serve())
