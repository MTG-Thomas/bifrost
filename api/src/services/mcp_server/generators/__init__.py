"""
MCP Tool Generators

Generate FastMCP tool wrappers from the central registry.
"""

from src.services.mcp_server.generators.fastmcp_generator import register_fastmcp_tools

__all__ = ["register_fastmcp_tools"]
