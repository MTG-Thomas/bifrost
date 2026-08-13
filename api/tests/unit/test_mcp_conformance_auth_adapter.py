"""Tests for the isolated MCP conformance authentication adapter."""

from __future__ import annotations

import jwt

from tests.conformance.auth_adapter import (
    MCP_RESOURCE,
    UPSTREAM_HOST,
    _authorization_header,
    _downstream_response_headers,
    _upstream_request_headers,
)


def test_adapter_mints_a_resource_bound_mcp_token() -> None:
    scheme, token = _authorization_header().split(" ", 1)
    claims = jwt.decode(token, options={"verify_signature": False})

    assert scheme == "Bearer"
    assert claims["aud"] == MCP_RESOURCE
    assert claims["resource"] == MCP_RESOURCE
    assert claims["scope"] == "mcp:access"
    assert claims["mcp"] is True


def test_adapter_only_replaces_auth_host_and_transport_headers() -> None:
    forwarded = _upstream_request_headers(
        [
            ("Host", "adapter:8080"),
            ("Authorization", "Bearer runner-value"),
            ("Content-Length", "123"),
            ("Connection", "keep-alive"),
            ("MCP-Protocol-Version", "2026-07-28"),
            ("Mcp-Name", "  bifrost_find_agents  "),
            ("X-Conformance-Test", "preserved"),
        ],
        "Bearer resource-bound-token",
    )

    assert forwarded == [
        ("MCP-Protocol-Version", "2026-07-28"),
        ("Mcp-Name", "bifrost_find_agents"),
        ("X-Conformance-Test", "preserved"),
        ("Authorization", "Bearer resource-bound-token"),
        ("Host", UPSTREAM_HOST),
    ]


def test_adapter_preserves_end_to_end_response_headers() -> None:
    forwarded = _downstream_response_headers(
        [
            ("content-type", "application/json"),
            ("mcp-session-id", "session-1"),
            ("content-length", "42"),
            ("transfer-encoding", "chunked"),
            ("connection", "close"),
        ]
    )

    assert forwarded == [
        (b"content-type", b"application/json"),
        (b"mcp-session-id", b"session-1"),
        (b"content-length", b"42"),
    ]
