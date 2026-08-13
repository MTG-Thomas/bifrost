"""OAuth issuer/resource binding for external MCP connections."""

from types import SimpleNamespace
from typing import Any

import pytest

from src.services.mcp_client.oauth_binding import resolve_oauth_binding


def _connection(metadata: dict) -> Any:
    server = SimpleNamespace(
        discovery_metadata=metadata,
        server_url="https://vendor.example.com/mcp",
    )
    return SimpleNamespace(server=server, server_url_override=None)


def test_separated_documents_are_authoritative_for_their_own_fields():
    connection = _connection(
        {
            "authorization_server_metadata": {
                "issuer": "https://issuer.example.com"
            },
            "protected_resource_metadata": {
                "resource": "https://resource.example.com/mcp",
                "issuer": "https://attacker.example.com",
            },
            "issuer": "https://attacker.example.com",
        }
    )

    assert resolve_oauth_binding(connection) == (
        "https://issuer.example.com",
        "https://resource.example.com/mcp",
    )


def test_separated_metadata_does_not_fall_back_to_conflicting_flattened_issuer():
    connection = _connection(
        {
            "authorization_server_metadata": None,
            "protected_resource_metadata": {
                "resource": "https://resource.example.com/mcp",
                "issuer": "https://attacker.example.com",
            },
            "issuer": "https://attacker.example.com",
        }
    )

    with pytest.raises(ValueError, match="missing issuer"):
        resolve_oauth_binding(connection)


def test_legacy_flattened_snapshot_remains_supported():
    connection = _connection(
        {
            "issuer": "https://issuer.example.com",
            "resource": "https://resource.example.com/mcp",
        }
    )

    assert resolve_oauth_binding(connection) == (
        "https://issuer.example.com",
        "https://resource.example.com/mcp",
    )
