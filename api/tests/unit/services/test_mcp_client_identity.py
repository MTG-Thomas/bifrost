"""Tests for MCP OAuth client display labels."""

import pytest

from src.services.mcp_server.mcp_client_identity import resolve_mcp_client_label


@pytest.mark.parametrize(
    ("redirect_uri", "client_name", "expected"),
    [
        ("cursor://anysphere.cursor-mcp/oauth/callback", None, "Cursor"),
        ("https://agents.cursor.com/oauth/callback", None, "Cursor"),
        ("https://claude.ai/api/mcp/auth_callback", None, "Claude"),
        ("claude://oauth/callback", None, "Claude Desktop"),
        ("http://127.0.0.1:1455/auth/callback", "codex", "Codex"),
        ("http://localhost:8080/callback", "Claude-Code", "Claude Code"),
        ("http://localhost:8080/callback", "Gemini_CLI", "Gemini CLI"),
        ("http://localhost:8080/callback", "Windsurf", "Windsurf"),
    ],
)
def test_resolves_known_clients(redirect_uri, client_name, expected):
    assert resolve_mcp_client_label(redirect_uri, client_name) == expected


@pytest.mark.parametrize(
    ("redirect_uri", "client_name"),
    [
        ("http://localhost:8080/callback", "Acme Internal Agent"),
        ("https://evil.example/cursor.com/callback", "Not Cursor"),
        ("https://[invalid/callback", "Cursor Support Portal"),
        (None, {"name": "Cursor"}),
    ],
)
def test_unknown_or_malformed_metadata_uses_generic_label(redirect_uri, client_name):
    assert resolve_mcp_client_label(redirect_uri, client_name) == "your MCP client"
