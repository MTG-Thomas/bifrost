from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

import httpx2  # type: ignore[reportMissingImports]
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.shared.exceptions import MCPError
from starlette.responses import JSONResponse, Response

from src.services.mcp_client import client as mcp_client


class _NegotiationPeer:
    def __init__(self, discover: str) -> None:
        self.discover = discover
        self.methods: list[str] = []

    async def __call__(self, scope, receive, send) -> None:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        payload = json.loads(body)
        method = payload["method"]
        self.methods.append(method)

        if method == "server/discover":
            if self.discover == "legacy":
                response = JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "error": {"code": -32601, "message": "Method not found"},
                    }
                )
            elif self.discover == "modern_error":
                response = JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "error": {
                            "code": -32022,
                            "message": "Unsupported protocol version",
                            "data": {
                                "supported": ["2099-01-01"],
                                "requested": "2026-07-28",
                            },
                        },
                    }
                )
            else:
                response = JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {
                            "supportedVersions": ["2026-07-28"],
                            "capabilities": {"tools": {}},
                            "resultType": "complete",
                            "ttlMs": 0,
                            "cacheScope": "private",
                        },
                    }
                )
        elif method == "initialize":
            response = JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "legacy-peer", "version": "1"},
                    },
                }
            )
        elif method == "notifications/initialized":
            response = Response(status_code=202)
        elif method == "tools/list":
            result: dict[str, Any] = {"tools": []}
            if self.discover == "modern":
                result.update(
                    resultType="complete",
                    ttlMs=0,
                    cacheScope="private",
                )
            response = JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": result,
                }
            )
        else:  # pragma: no cover - makes unexpected SDK traffic self-describing
            raise AssertionError(f"Unexpected method: {method}")

        await response(scope, receive, send)


def _http_transport(peer: _NegotiationPeer) -> StreamableHttpTransport:
    def factory(**kwargs: Any) -> httpx2.AsyncClient:
        kwargs.pop("follow_redirects", None)
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=peer),
            base_url="http://peer",
            follow_redirects=True,
            **kwargs,
        )

    return StreamableHttpTransport(
        "http://peer/mcp",
        httpx_client_factory=factory,
    )


@pytest.mark.asyncio
async def test_fastmcp_auto_negotiates_modern_first() -> None:
    peer = _NegotiationPeer("modern")
    client = Client(_http_transport(peer), mode="auto")

    async with client:
        await client.list_tools_mcp()
        assert client.protocol_version == "2026-07-28"
        assert client.initialize_result is None

    assert peer.methods == ["server/discover", "tools/list"]


@pytest.mark.asyncio
async def test_fastmcp_auto_falls_back_only_for_legacy_peer() -> None:
    peer = _NegotiationPeer("legacy")
    client = Client(_http_transport(peer), mode="auto")

    async with client:
        await client.list_tools_mcp()
        assert client.protocol_version == "2024-11-05"
        assert client.initialize_result is not None

    assert peer.methods == [
        "server/discover",
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]


@pytest.mark.asyncio
async def test_fastmcp_auto_surfaces_recognized_modern_version_error() -> None:
    peer = _NegotiationPeer("modern_error")
    client = Client(_http_transport(peer), mode="auto")

    with pytest.raises(MCPError) as exc_info:
        async with client:
            pass

    assert exc_info.value.code == -32022
    assert peer.methods == ["server/discover"]


@pytest.mark.asyncio
async def test_open_client_uses_auto_mode_and_logs_negotiated_path(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    connection = SimpleNamespace(
        id="connection-id",
        server_url_override=None,
        server=SimpleNamespace(server_url="https://peer.example/mcp"),
    )
    constructed: dict[str, Any] = {}

    class FakeClient:
        initialize_result = None
        protocol_version = "2026-07-28"

        def __init__(self, transport, **kwargs):
            constructed.update(transport=transport, **kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("fastmcp.Client", FakeClient)

    with caplog.at_level(logging.INFO):
        async with mcp_client.open_client(connection, "secret-token") as opened:
            assert isinstance(opened, FakeClient)

    assert constructed == {
        "transport": "https://peer.example/mcp",
        "auth": "secret-token",
        "mode": "auto",
    }
    assert "protocol_version=2026-07-28 path=modern_discover" in caplog.text
    assert "secret-token" not in caplog.text
