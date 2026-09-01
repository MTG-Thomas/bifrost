"""
ASGI middleware that extracts agent_id from /mcp/{agent_id} paths.

Intercepts requests to /mcp/{uuid}, stores the agent_id in the ASGI scope
as scope["mcp_agent_id"], and rewrites the path to /mcp so FastMCP's
route matcher works. Non-UUID paths (including /mcp/callback) pass through.
"""

import re
from uuid import UUID

from fastmcp.server.dependencies import get_http_request

# Match /mcp/{uuid} but not /mcp/callback or other non-UUID suffixes
_AGENT_PATH_RE = re.compile(
    r"^(/mcp)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(/.*)?$",
    re.IGNORECASE,
)

_OWS_PARSED_MCP_HEADERS = {
    b"mcp-method",
    b"mcp-name",
    b"mcp-protocol-version",
}


def get_scoped_agent_id() -> UUID | None:
    """Read the agent UUID written to the current FastMCP request scope."""
    try:
        agent_id = get_http_request().scope.get("mcp_agent_id")
        return UUID(agent_id) if agent_id else None
    except (RuntimeError, ValueError, AttributeError):
        # No HTTP context, malformed UUID, or an unexpected scope shape.
        return None


class AgentScopeMCPMiddleware:
    """ASGI middleware that rewrites /mcp/{agent_id} to /mcp."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            match = _AGENT_PATH_RE.match(path)
            if match:
                scope["mcp_agent_id"] = match.group(2)
                # Rewrite path to /mcp (preserving any trailing path like /mcp/sse)
                scope["path"] = match.group(1) + (match.group(3) or "")
        await self.app(scope, receive, send)


class MCPHeaderOWSMiddleware:
    """Parse transport OWS around MCP routing header field values.

    ASGI exposes header bytes after HTTP framing but Uvicorn/h11 can retain the
    optional SP/HTAB surrounding field values. RFC 9110 section 5.5 requires an
    intermediary or recipient to exclude that whitespace before evaluating the
    value. Normalize only MCP's standard routing headers before FastMCP applies
    its case-sensitive body/header validation.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope)
            scope["headers"] = [
                (name, value.strip(b" \t"))
                if name.lower() in _OWS_PARSED_MCP_HEADERS
                else (name, value)
                for name, value in scope.get("headers", [])
            ]
        await self.app(scope, receive, send)
