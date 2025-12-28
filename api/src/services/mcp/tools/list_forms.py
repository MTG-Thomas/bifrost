"""
List Forms MCP Tool

Lists forms in Bifrost with actionable URLs.
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


def list_forms_tool(context: MCPContext) -> Any:
    """
    Create a list_forms tool bound to the given context.

    Args:
        context: MCP context with user/org information

    Returns:
        Tool function for Claude Agent SDK
    """

    @tool(
        name="list_forms",
        description="List forms in Bifrost. Returns form details including links to view them in the platform.",
        input_schema={
            "type": "object",
            "properties": {
                "active_only": {
                    "type": "boolean",
                    "description": "If true, only show active forms (default: true)",
                },
            },
            "required": [],
        },
    )
    async def _list_forms(args: dict[str, Any]) -> dict[str, Any]:
        """
        List all forms.

        Args:
            args: Tool arguments containing:
                - active_only: Whether to show only active forms

        Returns:
            Dict with list of forms
        """
        from src.core.database import get_db_context
        from src.core.org_filter import OrgFilterType
        from src.repositories.forms import FormRepository

        active_only = args.get("active_only", True)

        logger.info(f"MCP list_forms called with active_only={active_only}")

        try:
            async with get_db_context() as db:
                # Determine filter type based on context
                if context.is_platform_admin or not context.org_id:
                    # Platform admin - show all forms
                    filter_type = OrgFilterType.ALL
                else:
                    # Org user - show their org + global forms
                    filter_type = OrgFilterType.ORG_PLUS_GLOBAL

                repo = FormRepository(db, context.org_id)
                forms = await repo.list_forms(
                    filter_type=filter_type,
                    active_only=active_only,
                )

                if not forms:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": "No forms found.\n\n"
                                "To create a form:\n"
                                "1. Create a workflow first\n"
                                "2. Create a form JSON file with `.form.json` extension\n"
                                "3. Use `validate_form_schema` to validate your form\n"
                                "4. Save the form to `/tmp/bifrost/workspace/forms/`",
                            }
                        ]
                    }

                # Format form list
                lines = ["# Forms\n"]
                lines.append(f"Found {len(forms)} form(s)\n")

                for form in forms:
                    lines.append(f"## {form.name}")
                    if form.description:
                        lines.append(f"{form.description}")

                    lines.append(f"- **ID:** `{form.id}`")

                    if form.workflow_id:
                        lines.append(f"- **Workflow ID:** `{form.workflow_id}`")

                    if form.launch_workflow_id:
                        lines.append(f"- **Launch Workflow ID:** `{form.launch_workflow_id}`")

                    status = "Active" if form.is_active else "Inactive"
                    lines.append(f"- **Status:** {status}")

                    if form.access_level:
                        lines.append(f"- **Access Level:** {form.access_level.value}")

                    # Count fields
                    if form.fields:
                        field_count = len(form.fields)
                        lines.append(f"- **Fields:** {field_count}")

                    # Include URL placeholder
                    lines.append(f"- **View/Edit:** `{{PLATFORM_URL}}/forms/{form.id}`")

                    if form.file_path:
                        lines.append(f"- **File:** `{form.file_path}`")

                    lines.append("")

                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "\n".join(lines),
                        }
                    ]
                }

        except Exception as e:
            logger.exception(f"Error listing forms via MCP: {e}")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error listing forms: {str(e)}",
                    }
                ]
            }

    return _list_forms
