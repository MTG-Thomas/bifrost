"""OAuth issuer/resource binding for external MCP connections."""

from __future__ import annotations

from typing import Any

from src.models.orm.external_mcp import MCPConnection


def resolve_oauth_binding(connection: MCPConnection) -> tuple[str, str]:
    """Return the configured authorization-server issuer and MCP resource.

    Discovery keeps the authorization-server and protected-resource documents
    separate so similarly named fields cannot overwrite one another.
    """
    server = connection.server
    metadata: dict[str, Any] = server.discovery_metadata or {}
    authorization_server = metadata.get("authorization_server_metadata") or {}
    protected_resource = metadata.get("protected_resource_metadata") or {}

    issuer = authorization_server.get("issuer") or metadata.get("issuer")
    resource = protected_resource.get("resource") or metadata.get("resource")
    if not isinstance(issuer, str) or not issuer:
        raise ValueError("MCP OAuth discovery metadata is missing issuer")
    if not isinstance(resource, str) or not resource:
        resource = connection.server_url_override or server.server_url
    return issuer, resource
