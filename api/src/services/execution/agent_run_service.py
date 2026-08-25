"""Agent run enqueue and result waiting."""
import json
import logging
from uuid import uuid4

import redis.asyncio as aioredis
from sqlalchemy import text

from src.core.cache.redis_client import get_redis
from src.core.log_safety import log_safe
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
    """Atomically publish or reuse one canonical agent-run identity.

    ``scheduled`` is the durable pre-broker state. A failed Redis or RabbitMQ
    publication leaves the row retryable, while the advisory transaction lock
    prevents concurrent callers from publishing the same run twice.
    """
    from uuid import UUID

    from src.core.database import get_db_context
    from src.models.orm.agent_runs import AgentRun

    if run_id is None:
        run_id = str(uuid4())

    created = False
    async with get_db_context() as db:
        await db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('bifrost:agent-run:' || :run_id))"
            ),
            {"run_id": run_id},
        )
        existing = await db.get(AgentRun, UUID(run_id))
        if existing is not None:
            if getattr(existing, "status", None) != "scheduled":
                return run_id, True
        else:
            db.add(AgentRun(
                id=UUID(run_id),
                agent_id=UUID(agent_id),
                trigger_type=trigger_type,
                trigger_source=trigger_source,
                event_delivery_id=(
                    UUID(event_delivery_id) if event_delivery_id else None
                ),
                input=input_data,
                output_schema=output_schema,
                status="scheduled",
                org_id=UUID(org_id) if org_id else None,
                caller_user_id=caller_user_id,
                caller_email=caller_email,
                caller_name=caller_name,
            ))
            await db.commit()
            created = True

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

    async with get_db_context() as db:
        await db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('bifrost:agent-run:' || :run_id))"
            ),
            {"run_id": run_id},
        )
        agent_run = await db.get(AgentRun, UUID(run_id))
        if agent_run is None or agent_run.status != "scheduled":
            return run_id, True

        # Hold the transaction-scoped claim through Redis and broker
        # confirmation. Any failure rolls the transaction back, preserving the
        # scheduled row for a retry with the same canonical run ID.
        redis_key = f"{REDIS_PREFIX}:{run_id}:context"
        async with get_redis() as redis:
            await redis.set(redis_key, json.dumps(context), ex=3600)

        message = {
            "run_id": run_id,
            "agent_id": agent_id,
            "trigger_type": trigger_type,
            "sync": sync,
        }
        await publish_message(QUEUE_NAME, message)
        agent_run.status = "queued"
        await db.commit()

    logger.info(f"Enqueued agent run {run_id} for agent {agent_id} (trigger={trigger_type})")
    return run_id, not created


async def get_pending_agent_run_context(run_id: str) -> dict | None:
    """Return the short-lived enqueue context before its AgentRun row exists."""
    redis_key = f"{REDIS_PREFIX}:{run_id}:context"
    async with get_redis() as redis:
        raw_context = await redis.get(redis_key)
    if raw_context is None:
        return None
    try:
        context = json.loads(raw_context)
    except (TypeError, json.JSONDecodeError):
        logger.warning("Invalid pending agent-run context for %s", log_safe(run_id))
        return None
    return context if isinstance(context, dict) else None


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
