"""Cross-replica synchronization for the dynamic workflow-tool catalog."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from src.core.cache.redis_client import get_redis
from src.core.pubsub import manager as pubsub_manager

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

WORKFLOW_CATALOG_CHANNEL = "mcp:workflow-catalog"
WORKFLOW_CATALOG_REVISION_KEY = "bifrost:mcp:workflow-catalog:revision"


async def get_workflow_catalog_revision() -> int:
    """Read the durable shared workflow-catalog revision."""
    async with get_redis() as redis:
        value = await redis.get(WORKFLOW_CATALOG_REVISION_KEY)
    return int(value or 0)


async def publish_workflow_catalog_changed() -> int:
    """Advance the shared revision and notify every subscribed API replica."""
    async with get_redis() as redis:
        revision = int(await redis.incr(WORKFLOW_CATALOG_REVISION_KEY))
        await redis.publish(
            f"bifrost:{WORKFLOW_CATALOG_CHANNEL}",
            json.dumps({
                "type": "mcp_workflow_catalog_changed",
                "revision": revision,
            }),
        )
    return revision


async def _handle_workflow_catalog_changed(message: dict[str, Any]) -> None:
    """Refresh this process when another replica advances the catalog."""
    revision = message.get("revision")
    if not isinstance(revision, int) or revision < 0:
        logger.warning("Ignoring invalid MCP workflow catalog revision: %r", revision)
        return

    from src.services.mcp_server.server import refresh_workflow_tools

    await refresh_workflow_tools(target_revision=revision)


async def ensure_workflow_catalog_current() -> None:
    """Reconcile a replica that may have missed a transient pub/sub message."""
    from src.services.mcp_server.server import refresh_workflow_tools

    revision = await get_workflow_catalog_revision()
    await refresh_workflow_tools(target_revision=revision)


async def start_workflow_catalog_sync(mcp: "FastMCP") -> int:
    """Subscribe this replica and load a revision-consistent catalog snapshot."""
    from src.services.mcp_server.server import refresh_workflow_tools

    await pubsub_manager.subscribe_internal(
        WORKFLOW_CATALOG_CHANNEL,
        _handle_workflow_catalog_changed,
    )
    try:
        return await refresh_workflow_tools(mcp=mcp, force=True)
    except BaseException:
        # Lifespan never reaches its ``finally`` block when startup fails, so
        # unwind this process-local registration here as well.
        stop_workflow_catalog_sync()
        raise


def stop_workflow_catalog_sync() -> None:
    """Unsubscribe this replica during MCP lifespan shutdown."""
    pubsub_manager.unsubscribe_internal(
        WORKFLOW_CATALOG_CHANNEL,
        _handle_workflow_catalog_changed,
    )
