"""
Bifrost MCP Server Module

Provides Model Context Protocol (MCP) server capabilities using FastMCP.
Designed for extensibility - supports user-facing MCP access via OAuth.

Usage:
    from src.services.mcp_server import BifrostMCPServer, MCPContext

    # Create server with context
    context = MCPContext(user_id=user.id, org_id=org.id)
    server = BifrostMCPServer(context)

    # Get FastMCP server for HTTP access
    fastmcp_server = server.get_fastmcp_server()
"""

from src.services.mcp_server.server import BifrostMCPServer, MCPContext

__all__ = ["BifrostMCPServer", "MCPContext"]
