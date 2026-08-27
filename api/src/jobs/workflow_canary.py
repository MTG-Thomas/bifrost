"""Publish one isolated inline execution and require a successful result."""

from __future__ import annotations

import asyncio
import base64
import os
from types import SimpleNamespace

from src.core.database import close_db, init_db
from src.core.redis_client import get_redis_client
from src.jobs.rabbitmq import rabbitmq
from src.services.execution.async_executor import enqueue_code_execution


def canary_queue_name() -> str:
    """Return the configured queue only when it is explicitly canary-scoped."""
    queue_name = os.environ.get("BIFROST_WORKFLOW_QUEUE_NAME", "")
    if not queue_name.endswith("-canary"):
        raise ValueError(
            "BIFROST_WORKFLOW_QUEUE_NAME must name an isolated -canary queue"
        )
    return queue_name


async def poison_depth(queue_name: str) -> int:
    """Read the isolated poison queue depth without consuming any messages."""
    await rabbitmq.init_pools()
    async with rabbitmq.get_connection() as connection:
        channel = await connection.channel()
        try:
            queue = await channel.declare_queue(
                f"{queue_name}-poison", passive=True
            )
            return int(queue.declaration_result.message_count)
        finally:
            await channel.close()


def require_successful_canary_result(result: dict | None) -> None:
    """Fail unless both the execution status and sentinel payload match."""
    if (
        result is None
        or result.get("status") != "Success"
        or result.get("result") != {"canary": "ok"}
    ):
        raise RuntimeError(f"isolated workflow canary failed: {result!r}")


async def run_canary() -> None:
    """Publish one synthetic execution and prove success without poison growth."""
    queue_name = canary_queue_name()
    context = SimpleNamespace(
        org_id=None,
        user_id="deployment-canary",
        name="Deployment Canary",
        email="deployment-canary@localhost",
        is_platform_admin=True,
        is_provider_org=False,
        is_external=False,
    )
    await init_db()
    try:
        poison_before = await poison_depth(queue_name)
        execution_id = await enqueue_code_execution(
            context,
            script_name="deployment_canary.py",
            code_base64=base64.b64encode(b"result = {'canary': 'ok'}\n").decode(),
            parameters={},
            sync=True,
            queue_name=queue_name,
        )
        result = await get_redis_client().wait_for_result(execution_id, timeout_seconds=90)
        require_successful_canary_result(result)
        poison_after = await poison_depth(queue_name)
        if poison_after > poison_before:
            raise RuntimeError(
                "isolated workflow canary grew poison queue: "
                f"before={poison_before} after={poison_after}"
            )
        print(f"isolated workflow canary passed: execution_id={execution_id}")
    finally:
        await rabbitmq.close()
        await close_db()


if __name__ == "__main__":
    asyncio.run(run_canary())
