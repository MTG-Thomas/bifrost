"""
Integration MCP Tools

Tools for listing available integrations.
"""

import logging
from typing import Any

from mcp.types import CallToolResult

from src.services.mcp_server.tool_decorator import system_tool
from src.services.mcp_server.tool_registry import ToolCategory
from src.services.mcp_server.tool_result import error_result, success_result

# MCPContext is imported where needed to avoid circular imports

logger = logging.getLogger(__name__)


@system_tool(
    id="list_integrations",
    name="List Integrations",
    description="List available integrations that can be used in workflows.",
    category=ToolCategory.INTEGRATION,
    default_enabled_for_coding_agent=True,
    is_restricted=True,
    input_schema={"type": "object", "properties": {}, "required": []},
)
async def list_integrations(context: Any) -> CallToolResult:
    """List all available integrations."""
    from sqlalchemy import select

    from src.core.database import get_db_context
    from src.models.orm.integrations import Integration, IntegrationMapping

    logger.info("MCP list_integrations called")

    try:
        async with get_db_context() as db:
            if context.is_platform_admin or not context.org_id:
                result = await db.execute(
                    select(Integration)
                    .where(Integration.is_deleted.is_(False))
                    .order_by(Integration.name)
                )
                integrations = result.scalars().all()
            else:
                result = await db.execute(
                    select(Integration)
                    .join(IntegrationMapping)
                    .where(IntegrationMapping.organization_id == context.org_id)
                    .where(Integration.is_deleted.is_(False))
                    .order_by(Integration.name)
                )
                integrations = result.scalars().all()

            integration_list = [
                {
                    "name": integration.name,
                    "has_oauth": integration.has_oauth_config,
                    "entity_id_name": integration.entity_id_name,
                }
                for integration in integrations
            ]

            display_text = f"Found {len(integration_list)} integration(s)"
            return success_result(
                display_text, {"integrations": integration_list, "count": len(integration_list)}
            )

    except Exception as e:
        logger.exception(f"Error listing integrations via MCP: {e}")
        return error_result(f"Error listing integrations: {str(e)}")
