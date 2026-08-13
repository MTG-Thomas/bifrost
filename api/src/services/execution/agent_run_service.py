"""Agent run enqueue and result waiting."""
import json
import logging
from uuid import uuid4

import redis.asyncio as aioredis
from sqlalchemy import text

from src.core.cache.redis_client import get_redis
from src.jobs.rabbitmq import publish_message

logger = logging.getLogger(__name__)

QUEUE_NAME = "agent-runs"
REDIS_PREFIX = "bifrost:agent_run"


async def enqueue_agent_run(
    agent_id: str,
    trigger_type: str,
    input_data: dict | None = None,
    *,
    trigger_source: str | None = None,
    output_schema: dict | None = None,
    org_id: str | None = None,
    caller_user_id: str | None = None,
    caller_email: str | None = None,
    caller_name: str | None = None,
    event_delivery_id: str | None = None,
    sync: bool = False,
    run_id: str | None = None,
) -> str:
    """Enqueue an agent run for worker processing. Returns run_id."""
    queued_id, _reused = await enqueue_agent_run_once(
        agent_id=agent_id,
        trigger_type=trigger_type,
        input_data=input_data,
        trigger_source=trigger_source,
        output_schema=output_schema,
        org_id=org_id,
        caller_user_id=caller_user_id,
        caller_email=caller_email,
        caller_name=caller_name,
        event_delivery_id=event_delivery_id,
        sync=sync,
        run_id=run_id,
    )
    return queued_id


async def enqueue_agent_run_once(
    agent_id: str,
    trigger_type: str,
    input_data: dict | None = None,
    *,
    trigger_source: str | None = None,
    output_schema: dict | None = None,
    org_id: str | None = None,
    caller_user_id: str | None = None,
    caller_email: str | None = None,
    caller_name: str | None = None,
    event_delivery_id: str | None = None,
    sync: bool = False,
    run_id: str | None = None,
) -> tuple[str, bool]:
    """Atomically enqueue or reuse one canonical agent-run identity."""
    from uuid import UUID

    from src.core.database import get_db_context
    from src.models.orm.agent_runs import AgentRun

    if run_id is None:
        run_id = str(uuid4())

    async with get_db_context() as db:
        await db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('bifrost:agent-run:' || :run_id))"
            ),
            {"run_id": run_id},
        )
        if await db.get(AgentRun, UUID(run_id)) is not None:
            return run_id, True
        db.add(
            AgentRun(
                id=UUID(run_id),
                agent_id=UUID(agent_id),
                trigger_type=trigger_type,
                trigger_source=trigger_source,
                event_delivery_id=(
                    UUID(event_delivery_id) if event_delivery_id else None
                ),
                input=input_data,
                output_schema=output_schema,
                status="queued",
                org_id=UUID(org_id) if org_id else None,
                caller_user_id=caller_user_id,
                caller_email=caller_email,
                caller_name=caller_name,
            )
        )
        await db.commit()

    context = {
        "run_id": run_id,
        "agent_id": agent_id,
        "trigger_type": trigger_type,
        "trigger_source": trigger_source,
        "input": input_data,
        "output_schema": output_schema,
        "org_id": org_id,
        "caller": {
            "user_id": caller_user_id,
            "email": caller_email,
            "name": caller_name,
            "organization_id": org_id,
        },
        "event_delivery_id": event_delivery_id,
        "sync": sync,
        "cancelled": False,
    }

    # Store full context in Redis
    redis_key = f"{REDIS_PREFIX}:{run_id}:context"
    async with get_redis() as redis:
        await redis.set(redis_key, json.dumps(context), ex=3600)

    # Publish lightweight message to queue
    message = {
        "run_id": run_id,
        "agent_id": agent_id,
        "trigger_type": trigger_type,
        "sync": sync,
    }
    await publish_message(QUEUE_NAME, message)

    logger.info(f"Enqueued agent run {run_id} for agent {agent_id} (trigger={trigger_type})")
    return run_id, False


async def wait_for_agent_run_result(run_id: str, timeout: int = 1800) -> dict | None:
    """Block until agent run completes. Used for sync SDK calls.

    Uses a dedicated Redis connection with a socket_timeout that covers
    the full BLPOP wait (the default 5s socket_timeout in get_redis()
    kills the connection before the worker can push a result).
    """
    from src.config import get_settings

    result_key = f"{REDIS_PREFIX}:{run_id}:result"
    # socket_timeout must exceed the BLPOP timeout so the connection
    # stays alive for the entire blocking wait, plus a small buffer.
    client = aioredis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_timeout=float(timeout + 10),
        socket_connect_timeout=5.0,
    )
    try:
        result = await client.blpop(result_key, timeout=timeout)  # pyright: ignore[reportGeneralTypeIssues]
        if result:
            return json.loads(result[1])
        return None
    finally:
        await client.aclose()
