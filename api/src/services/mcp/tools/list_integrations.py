"""
List Integrations MCP Tool

Allows Claude Agent SDK to discover available integrations.
Returns integration names and descriptions (not secrets/credentials).
"""

import logging
from typing import Any

from src.services.mcp.server import MCPContext

logger = logging.getLogger(__name__)

# Claude Agent SDK is optional - will be installed when using coding mode
try:
    from claude_agent_sdk import tool  # type: ignore

    HAS_CLAUDE_SDK = True
except ImportError:
    HAS_CLAUDE_SDK = False

    def tool(**kwargs: Any) -> Any:
        """Stub decorator when SDK not installed."""

        def decorator(func: Any) -> Any:
            return func

        return decorator


def list_integrations_tool(context: MCPContext) -> Any:
    """
    Create a list_integrations tool bound to the given context.

    Args:
        context: MCP context with user/org information

    Returns:
        Tool function for Claude Agent SDK
    """

    @tool(
        name="list_integrations",
        description="List available integrations that can be used in workflows. Returns integration names and descriptions.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    async def _list_integrations(args: dict[str, Any]) -> dict[str, Any]:
        """
        List all available integrations.

        Returns:
            Dict with list of integration names and descriptions
        """
        from sqlalchemy import select

        from src.core.database import get_db_context
        from src.models.orm.integrations import Integration, IntegrationMapping

        logger.info("MCP list_integrations called")

        try:
            async with get_db_context() as db:
                # Get integrations available to this user's context
                # Platform admins see all integrations
                # Org users see integrations mapped to their org

                if context.is_platform_admin or not context.org_id:
                    # Platform admin - show all non-deleted integrations
                    result = await db.execute(
                        select(Integration)
                        .where(Integration.is_deleted.is_(False))
                        .order_by(Integration.name)
                    )
                    integrations = result.scalars().all()
                else:
                    # Org user - show integrations mapped to their org
                    result = await db.execute(
                        select(Integration)
                        .join(IntegrationMapping)
                        .where(IntegrationMapping.organization_id == context.org_id)
                        .where(Integration.is_deleted.is_(False))
                        .order_by(Integration.name)
                    )
                    integrations = result.scalars().all()

                if not integrations:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": "No integrations are currently configured.\n\n"
                                "To use integrations in workflows, they must first be set up "
                                "in the Bifrost admin panel.",
                            }
                        ]
                    }

                # Format integration list
                lines = ["# Available Integrations\n"]
                for integration in integrations:
                    lines.append(f"## {integration.name}")
                    # Check OAuth configuration
                    if integration.has_oauth_config:
                        lines.append("- **Auth:** OAuth configured")
                    if integration.entity_id_name:
                        lines.append(f"- **Entity:** {integration.entity_id_name}")
                    lines.append("")

                lines.append("\n## Usage in Workflows\n")
                lines.append("```python")
                lines.append("from bifrost import integrations")
                lines.append("")
                lines.append('integration = await integrations.get("IntegrationName")')
                lines.append("if integration and integration.oauth:")
                lines.append("    access_token = integration.oauth.access_token")
                lines.append("```")

                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "\n".join(lines),
                        }
                    ]
                }

        except Exception as e:
            logger.exception(f"Error listing integrations via MCP: {e}")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error listing integrations: {str(e)}",
                    }
                ]
            }

    return _list_integrations
