"""Test-only authenticated transport adapter for the official MCP runner.

The upstream conformance CLI cannot attach an Authorization header to server
requests. This adapter runs only on the isolated Compose test network and
forwards request body bytes to Bifrost's real ``/mcp`` endpoint while adding
resource-bound ``Authorization`` and canonical ``Host`` headers. It does not
inspect or rewrite MCP payloads, and it does not bypass Bifrost authentication
or dispatch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from tests.fixtures.auth import create_test_jwt


UPSTREAM_ORIGIN = "http://api:8000"
MCP_RESOURCE = f"{UPSTREAM_ORIGIN}/mcp"
UPSTREAM_HOST = "api:8000"

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _authorization_header() -> str:
    token = create_test_jwt(is_superuser=True, mcp_resource=MCP_RESOURCE)
    return f"Bearer {token}"


def _upstream_request_headers(
    headers: Iterable[tuple[str, str]], authorization: str
) -> list[tuple[str, str]]:
    forwarded = [
        # RFC 9110 section 5.5 defines leading/trailing SP/HTAB as optional
        # whitespace outside the field value. A forwarding intermediary must
        # not turn that transport syntax into application data.
        (name, value.strip(" \t"))
        for name, value in headers
        if name.lower()
        not in _HOP_BY_HOP_HEADERS | {"authorization", "content-length", "host"}
    ]
    forwarded.extend(
        [
            ("Authorization", authorization),
            ("Host", UPSTREAM_HOST),
        ]
    )
    return forwarded


def _downstream_response_headers(
    headers: Iterable[tuple[str, str]],
) -> list[tuple[bytes, bytes]]:
    return [
        (name.encode("latin-1"), value.encode("latin-1"))
        for name, value in headers
        if name.lower() not in _HOP_BY_HOP_HEADERS
    ]


@asynccontextmanager
async def _lifespan(app: Starlette) -> AsyncIterator[None]:
    app.state.authorization = _authorization_header()
    app.state.client = httpx.AsyncClient(timeout=None)
    try:
        yield
    finally:
        await app.state.client.aclose()


async def _health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def _proxy(request: Request) -> StreamingResponse:
    body = await request.body()
    query = f"?{request.url.query}" if request.url.query else ""
    upstream_request = request.app.state.client.build_request(
        request.method,
        f"{UPSTREAM_ORIGIN}{request.url.path}{query}",
        headers=_upstream_request_headers(
            (
                (name.decode("latin-1"), value.decode("latin-1"))
                for name, value in request.headers.raw
            ),
            request.app.state.authorization,
        ),
        content=body,
    )
    upstream_response = await request.app.state.client.send(
        upstream_request, stream=True
    )

    async def response_body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream_response.aiter_raw():
                yield chunk
        finally:
            await upstream_response.aclose()

    response = StreamingResponse(
        response_body(),
        status_code=upstream_response.status_code,
    )
    response.raw_headers = _downstream_response_headers(
        upstream_response.headers.multi_items()
    )
    return response


app = Starlette(
    routes=[
        Route("/health", _health, methods=["GET"]),
        Route(
            "/{path:path}",
            _proxy,
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        ),
    ],
    lifespan=_lifespan,
)
