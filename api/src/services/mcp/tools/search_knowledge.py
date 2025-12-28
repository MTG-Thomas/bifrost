"""
Search Knowledge MCP Tool

Searches the Bifrost knowledge base for documentation and examples.
Wraps the existing knowledge repository with embedding-based search.
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


def search_knowledge_tool(context: MCPContext) -> Any:
    """
    Create a search_knowledge tool bound to the given context.

    Args:
        context: MCP context with user/org information

    Returns:
        Tool function for Claude Agent SDK
    """

    @tool(
        name="search_knowledge",
        description="Search the Bifrost knowledge base for documentation and examples. Use this to find information about SDK features, best practices, and troubleshooting.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query to find relevant documentation",
                },
                "namespace": {
                    "type": "string",
                    "description": "Knowledge namespace to search (default: bifrost_docs)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 5, max: 10)",
                },
            },
            "required": ["query"],
        },
    )
    async def _search_knowledge(args: dict[str, Any]) -> dict[str, Any]:
        """
        Search the knowledge base.

        Args:
            args: Tool arguments containing:
                - query: Search query
                - namespace: Knowledge namespace (default: bifrost_docs)
                - limit: Max results (default: 5)

        Returns:
            Dict with search results
        """
        from src.core.database import get_db_context
        from src.repositories.knowledge import KnowledgeRepository
        from src.services.embeddings import get_embedding_client

        query = args.get("query")
        namespace = args.get("namespace", "bifrost_docs")
        limit = min(args.get("limit", 5), 10)  # Cap at 10 results

        if not query:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Error: query is required",
                    }
                ]
            }

        logger.info(f"MCP search_knowledge called with query='{query}', namespace='{namespace}'")

        try:
            async with get_db_context() as db:
                # Get embedding client
                try:
                    embedding_client = await get_embedding_client(db)
                except ValueError as e:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Knowledge search unavailable: {str(e)}\n\n"
                                "Embedding configuration is required for semantic search.",
                            }
                        ]
                    }

                # Generate embedding for query
                query_embeddings = await embedding_client.embed([query])
                if not query_embeddings:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": "Error: Failed to generate query embedding",
                            }
                        ]
                    }

                query_embedding = query_embeddings[0]

                # Search knowledge store
                repo = KnowledgeRepository(db)
                results = await repo.search(
                    query_embedding=query_embedding,
                    namespace=namespace,
                    organization_id=context.org_id,
                    limit=limit,
                    fallback=True,  # Include global knowledge
                )

                if not results:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": f"No results found for: **{query}**\n\n"
                                f"Searched in namespace: `{namespace}`\n\n"
                                "Try different search terms or check if the knowledge base has been populated.",
                            }
                        ]
                    }

                # Format results
                lines = ["# Knowledge Search Results\n"]
                lines.append(f"Query: **{query}**\n")
                lines.append(f"Found {len(results)} result(s) in namespace `{namespace}`\n")
                lines.append("---\n")

                for i, doc in enumerate(results, 1):
                    # Show score as percentage
                    score_pct = f"{(doc.score or 0) * 100:.1f}%"

                    lines.append(f"## Result {i} (Relevance: {score_pct})\n")

                    # Include metadata if available
                    if doc.metadata:
                        meta_parts = []
                        if doc.metadata.get("source"):
                            meta_parts.append(f"Source: {doc.metadata['source']}")
                        if doc.metadata.get("title"):
                            meta_parts.append(f"Title: {doc.metadata['title']}")
                        if meta_parts:
                            lines.append(f"*{' | '.join(meta_parts)}*\n")

                    # Content - truncate if very long
                    content = doc.content
                    if len(content) > 2000:
                        content = content[:2000] + "...\n\n*(truncated)*"

                    lines.append(content)
                    lines.append("\n---\n")

                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "\n".join(lines),
                        }
                    ]
                }

        except Exception as e:
            logger.exception(f"Error searching knowledge via MCP: {e}")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error searching knowledge base: {str(e)}",
                    }
                ]
            }

    return _search_knowledge
