"""
List Workflows MCP Tool

Lists workflows registered in Bifrost. Useful for verifying that
workflows created in the workspace were discovered and registered.
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


def list_workflows_tool(context: MCPContext) -> Any:
    """
    Create a list_workflows tool bound to the given context.

    Args:
        context: MCP context with user/org information

    Returns:
        Tool function for Claude Agent SDK
    """

    @tool(
        name="list_workflows",
        description="List workflows registered in Bifrost. Use this to verify a workflow you created in /tmp/bifrost/workspace was successfully discovered and registered by the platform.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional search query to filter workflows by name or description",
                },
                "category": {
                    "type": "string",
                    "description": "Optional category to filter workflows",
                },
            },
            "required": [],
        },
    )
    async def _list_workflows(args: dict[str, Any]) -> dict[str, Any]:
        """
        List all registered workflows.

        Args:
            args: Tool arguments containing:
                - query: Optional search query
                - category: Optional category filter

        Returns:
            Dict with list of workflows
        """
        from src.core.database import get_db_context
        from src.repositories.workflows import WorkflowRepository

        query = args.get("query")
        category = args.get("category")

        logger.info(f"MCP list_workflows called with query={query}, category={category}")

        try:
            async with get_db_context() as db:
                repo = WorkflowRepository(db)

                # Search with filters
                workflows = await repo.search(
                    query=query,
                    category=category,
                    limit=100,
                )

                total_count = await repo.count_active()

                if not workflows:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": "No workflows found.\n\n"
                                "If you've created a workflow file in `/tmp/bifrost/workspace`, "
                                "wait a moment for the file watcher to detect and register it.\n\n"
                                "Workflows are Python files with the `.workflow.py` extension that "
                                "use the `@workflow` decorator.",
                            }
                        ]
                    }

                # Format workflow list
                lines = ["# Registered Workflows\n"]
                lines.append(f"Showing {len(workflows)} of {total_count} total workflows\n")

                for workflow in workflows:
                    lines.append(f"## {workflow.name}")
                    if workflow.description:
                        lines.append(f"{workflow.description}")

                    # Metadata
                    meta_parts = []
                    if workflow.category:
                        meta_parts.append(f"Category: {workflow.category}")
                    if workflow.is_tool:
                        meta_parts.append("Tool: Yes")
                    if workflow.schedule:
                        meta_parts.append(f"Schedule: {workflow.schedule}")
                    if workflow.endpoint_enabled:
                        meta_parts.append("Endpoint: Enabled")

                    if meta_parts:
                        lines.append(f"- {' | '.join(meta_parts)}")

                    if workflow.file_path:
                        lines.append(f"- File: `{workflow.file_path}`")

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
            logger.exception(f"Error listing workflows via MCP: {e}")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error listing workflows: {str(e)}",
                    }
                ]
            }

    return _list_workflows
