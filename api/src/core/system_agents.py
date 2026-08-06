"""Security policy helpers for built-in MCP agent tools."""

from __future__ import annotations


# Agent-management MCP tools can create, modify, or remove agents and their
# tool grants. They are safe for direct admin-controlled MCP use, but not as
# chat-agent callable tools.
PRIVILEGED_AGENT_MANAGEMENT_TOOLS: frozenset[str] = frozenset({
    "create_agent",
    "update_agent",
    "delete_agent",
})

# Built-in tools that remain platform-admin-only even when an accessible agent
# lists them. Agent assignment must never override this authorization boundary.
PLATFORM_ADMIN_SYSTEM_TOOLS: frozenset[str] = frozenset({
    "create_agent",
    "create_app",
    "create_form",
    "create_organization",
    "create_table",
    "create_workflow",
    "delete_agent",
    "delete_content",
    "execute_workflow",
    "get_agent_schema",
    "get_app_schema",
    "get_data_provider_schema",
    "get_execution",
    "get_form_schema",
    "get_organization",
    "get_sdk_schema",
    "get_table",
    "get_table_schema",
    "get_workflow",
    "get_workflow_schema",
    "list_executions",
    "list_integrations",
    "list_organizations",
    "list_tables",
    "list_workflows",
    "patch_content",
    "publish_app",
    "replace_content",
    "update_agent",
    "update_app",
    "update_form",
    "update_table",
    "validate_workflow",
})


def is_privileged_agent_management_tool(tool_name: str) -> bool:
    """Return True when a built-in MCP tool manages agent privileges."""
    return tool_name in PRIVILEGED_AGENT_MANAGEMENT_TOOLS
