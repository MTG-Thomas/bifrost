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
    has_separated_metadata = (
        "authorization_server_metadata" in metadata
        or "protected_resource_metadata" in metadata
    )
    if has_separated_metadata:
        authorization_server = metadata.get("authorization_server_metadata")
        protected_resource = metadata.get("protected_resource_metadata")
        authorization_server = (
            authorization_server
            if isinstance(authorization_server, dict)
            else {}
        )
        protected_resource = (
            protected_resource
            if isinstance(protected_resource, dict)
            else {}
        )
        issuer = authorization_server.get("issuer")
        resource = protected_resource.get("resource")
    else:
        # Snapshots created before the documents were preserved separately
        # only have flattened fields. Keep those usable without letting a
        # conflicting field in a new protected-resource document substitute
        # for authorization-server metadata.
        issuer = metadata.get("issuer")
        resource = metadata.get("resource")
    if not isinstance(issuer, str) or not issuer:
        raise ValueError("MCP OAuth discovery metadata is missing issuer")
    if not isinstance(resource, str) or not resource:
        resource = connection.server_url_override or server.server_url
    return issuer, resource
