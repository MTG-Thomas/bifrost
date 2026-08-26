"""Operational CLI for RabbitMQ poison queues.

Run inside the API/worker environment:

    python -m src.jobs.dlq_cli inspect workflow-executions --limit 10
    python -m src.jobs.dlq_cli replay workflow-executions --limit 5 --dry-run
    python -m src.jobs.dlq_cli discard workflow-executions --limit 5 --reason "bad payload"
"""

import argparse
import asyncio
import hashlib
import json
import logging
from typing import Any

import aio_pika

from src.config import get_settings
from src.jobs.rabbitmq import _message_headers
from src.jobs.execution_policy import broker_execution_policies

logger = logging.getLogger(__name__)
MAX_REPLAY_COUNT = 3


def _validate_queue(queue: str, *, replaying: bool = False) -> None:
    policy = broker_execution_policies().get(queue)
    if policy is None:
        raise ValueError(f"unknown poison queue: {queue}")
    if replaying and not policy.replay_allowed:
        raise ValueError(f"policy forbids replay for queue: {queue}")


async def _record_disposition(
    queue: str,
    message: Any,
    *,
    action: str,
    actor: str,
    reason: str,
    replay_count: int,
) -> None:
    from src.core.database import get_db_context
    from src.models.orm.poison_message_dispositions import PoisonMessageDisposition

    headers = dict(message.headers or {})
    async with get_db_context() as db:
        db.add(
            PoisonMessageDisposition(
                queue_name=queue,
                message_id=message.message_id,
                idempotency_key=headers.get("x-idempotency-key"),
                action=action,
                actor=(actor.strip() or "unknown")[:255],
                reason=(reason.strip() or "operator request")[:2000],
                retry_count=int(headers.get("x-retry-count") or 0),
                replay_count=replay_count,
                body_sha256=hashlib.sha256(message.body).hexdigest(),
            )
        )
        await db.commit()


def decode_message(body: bytes) -> dict[str, Any] | str:
    try:
        parsed = json.loads(body.decode())
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:
        return body.decode(errors="replace")


async def _connect():
    return await aio_pika.connect_robust(get_settings().rabbitmq_url)


async def _fetch_poison_messages(poison, limit: int) -> list[Any]:
    messages: list[Any] = []
    for _ in range(limit):
        message = await poison.get(fail=False, no_ack=False)
        if message is None:
            break
        messages.append(message)
    return messages


async def _requeue_messages(messages: list[Any]) -> None:
    for message in messages:
        await message.nack(requeue=True)


async def _ensure_main_queue(channel, queue_name: str) -> None:
    await channel.declare_queue(queue_name, passive=True)


async def inspect(queue: str, limit: int) -> list[dict[str, Any]]:
    _validate_queue(queue)
    connection = await _connect()
    async with connection:
        channel = await connection.channel()
        poison = await channel.declare_queue(f"{queue}-poison", durable=True)
        messages = await _fetch_poison_messages(poison, limit)
        rows = [_describe(queue, message) for message in messages]
        await _requeue_messages(messages)
        await channel.close()
    return rows


async def replay(
    queue: str,
    limit: int,
    dry_run: bool,
    *,
    actor: str = "local-operator",
    reason: str = "manual replay",
) -> list[dict[str, Any]]:
    _validate_queue(queue, replaying=True)
    rows: list[dict[str, Any]] = []
    connection = await _connect()
    async with connection:
        channel = await connection.channel()
        poison = await channel.declare_queue(f"{queue}-poison", durable=True)
        await _ensure_main_queue(channel, queue)
        messages = await _fetch_poison_messages(poison, limit)
        for message in messages:
            row = _describe(queue, message)
            rows.append(row)
            if dry_run:
                continue
            body = decode_message(message.body)
            publish_body = body if isinstance(body, dict) else {"_malformed_body": body}
            replay_count = int((message.headers or {}).get("x-replayed-count") or 0) + 1
            if replay_count > MAX_REPLAY_COUNT:
                row["skipped"] = "replay_count_exhausted"
                await message.nack(requeue=True)
                continue
            await _record_disposition(
                queue,
                message,
                action="replay",
                actor=actor,
                reason=reason,
                replay_count=replay_count,
            )
            headers = _message_headers(
                publish_body,
                queue,
                message_id=message.message_id,
                headers=dict(message.headers or {}),
                retry_count=0,
                replay_count=replay_count,
            )
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(publish_body).encode(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=message.message_id,
                    correlation_id=message.correlation_id,
                    headers=headers,
                ),
                routing_key=queue,
            )
            await message.ack()
            logger.info("Replayed poison message", extra={"queue": queue, **row})
        if dry_run:
            await _requeue_messages(messages)
        await channel.close()
    return rows


async def discard(
    queue: str,
    limit: int,
    reason: str,
    dry_run: bool,
    *,
    actor: str = "local-operator",
) -> list[dict[str, Any]]:
    _validate_queue(queue)
    rows: list[dict[str, Any]] = []
    connection = await _connect()
    async with connection:
        channel = await connection.channel()
        poison = await channel.declare_queue(f"{queue}-poison", durable=True)
        messages = await _fetch_poison_messages(poison, limit)
        for message in messages:
            row = _describe(queue, message)
            rows.append(row)
            if dry_run:
                continue
            await _record_disposition(
                queue,
                message,
                action="discard",
                actor=actor,
                reason=reason,
                replay_count=int(
                    (message.headers or {}).get("x-replayed-count") or 0
                ),
            )
            await message.ack()
            logger.warning(
                "Discarded poison message",
                extra={"queue": queue, "discard_reason": reason, **row},
            )
        if dry_run:
            await _requeue_messages(messages)
        await channel.close()
    return rows


def _describe(queue: str, message) -> dict[str, Any]:
    headers = dict(message.headers or {})
    return {
        "queue": queue,
        "poison_queue": f"{queue}-poison",
        "message_id": message.message_id,
        "correlation_id": message.correlation_id,
        "idempotency_key": headers.get("x-idempotency-key"),
        "retry_count": headers.get("x-retry-count", 0),
        "replay_count": headers.get("x-replayed-count", 0),
        "origin_queue": headers.get("x-origin-queue"),
        "dead_letter": headers.get("x-death"),
        "headers": headers,
        "body": decode_message(message.body),
        "body_sha256": hashlib.sha256(message.body).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect/replay/discard Bifrost RabbitMQ poison queues")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "replay", "discard"):
        cmd = sub.add_parser(name)
        cmd.add_argument("queue")
        cmd.add_argument("--limit", type=int, default=10)
        cmd.add_argument("--dry-run", action="store_true")
    for name in ("replay", "discard"):
        sub.choices[name].add_argument("--actor", required=True)
        sub.choices[name].add_argument("--reason", required=True)
    args = parser.parse_args(argv)

    if args.command == "inspect":
        rows = asyncio.run(inspect(args.queue, args.limit))
    elif args.command == "replay":
        rows = asyncio.run(
            replay(
                args.queue,
                args.limit,
                args.dry_run,
                actor=args.actor,
                reason=args.reason,
            )
        )
    else:
        rows = asyncio.run(
            discard(
                args.queue,
                args.limit,
                args.reason,
                args.dry_run,
                actor=args.actor,
            )
        )
    print(json.dumps(rows, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
