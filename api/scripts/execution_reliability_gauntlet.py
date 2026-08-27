"""Disposable real-service gauntlet for Bifrost delivery invariants.

This module is intentionally test-environment-only.  It uses a unique RabbitMQ
namespace and a disposable PostgreSQL table, and refuses to run unless both the
environment and database name are unmistakably test-scoped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import aio_pika
import asyncpg
from redis.asyncio import Redis
from src.jobs.rabbitmq import (
    BaseConsumer,
    DuplicateMessage,
    RetryableConsumerError,
    publish_message,
    rabbitmq,
)


@dataclass
class ScenarioResult:
    name: str
    status: str
    duration_seconds: float
    evidence: dict[str, Any]


def _database_dsn() -> str:
    value = os.environ.get("BIFROST_DATABASE_URL_SYNC") or os.environ.get(
        "BIFROST_DATABASE_URL", ""
    )
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _require_safe_environment(queue_prefix: str) -> None:
    environment = os.environ.get("BIFROST_ENVIRONMENT", "").lower()
    database = _database_dsn().split("?", 1)[0].rsplit("/", 1)[-1]
    if environment != "testing":
        raise RuntimeError("reliability gauntlet requires BIFROST_ENVIRONMENT=testing")
    if not database.endswith("_test"):
        raise RuntimeError("reliability gauntlet requires a *_test PostgreSQL database")
    if not queue_prefix.startswith("bifrost-reliability-"):
        raise RuntimeError("reliability queue namespace is not isolated")


async def _db() -> asyncpg.Connection:
    return await asyncpg.connect(_database_dsn())


async def _prepare_table() -> None:
    connection = await _db()
    try:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS bifrost_reliability_claims (
                run_id text NOT NULL,
                scenario text NOT NULL,
                idempotency_key text NOT NULL,
                claims integer NOT NULL DEFAULT 1,
                effects integer NOT NULL DEFAULT 1,
                PRIMARY KEY (run_id, scenario, idempotency_key)
            )
            """
        )
    finally:
        await connection.close()


async def _claim(run_id: str, scenario: str, key: str) -> bool:
    connection = await _db()
    try:
        row = await connection.fetchrow(
            """
            INSERT INTO bifrost_reliability_claims
                (run_id, scenario, idempotency_key)
            VALUES ($1, $2, $3)
            ON CONFLICT (run_id, scenario, idempotency_key)
            DO UPDATE SET claims = bifrost_reliability_claims.claims + 1
            RETURNING claims, effects
            """,
            run_id,
            scenario,
            key,
        )
        return bool(row and row["claims"] == 1)
    finally:
        await connection.close()


async def _counts(run_id: str, scenario: str) -> dict[str, int]:
    connection = await _db()
    try:
        row = await connection.fetchrow(
            """
            SELECT COALESCE(sum(claims), 0)::int AS claims,
                   COALESCE(sum(effects), 0)::int AS effects,
                   count(*)::int AS identities
            FROM bifrost_reliability_claims
            WHERE run_id = $1 AND scenario = $2
            """,
            run_id,
            scenario,
        )
        return dict(row or {})
    finally:
        await connection.close()


async def _delete_run(run_id: str) -> None:
    connection = await _db()
    try:
        await connection.execute(
            "DELETE FROM bifrost_reliability_claims WHERE run_id = $1", run_id
        )
    finally:
        await connection.close()


async def _drop_table() -> None:
    connection = await _db()
    try:
        await connection.execute("DROP TABLE IF EXISTS bifrost_reliability_claims")
    finally:
        await connection.close()


async def _queue_snapshot(queue_name: str) -> dict[str, dict[str, int]]:
    connection = await aio_pika.connect_robust(os.environ["BIFROST_RABBITMQ_URL"])
    try:
        channel = await connection.channel()
        result: dict[str, dict[str, int]] = {}
        for name in (queue_name, f"{queue_name}-retry-1", f"{queue_name}-poison"):
            try:
                queue = await channel.declare_queue(name, passive=True)
            except aio_pika.exceptions.ChannelClosed:
                channel = await connection.channel()
                result[name] = {"ready": 0, "consumers": 0}
                continue
            declaration = queue.declaration_result
            result[name] = {
                "ready": int(declaration.message_count),
                "consumers": int(declaration.consumer_count),
            }
        return result
    finally:
        await connection.close()


async def _delete_topology(queue_name: str) -> None:
    connection = await aio_pika.connect_robust(os.environ["BIFROST_RABBITMQ_URL"])
    try:
        channel = await connection.channel()
        for name in (queue_name, f"{queue_name}-retry-1", f"{queue_name}-poison"):
            try:
                await channel.queue_delete(name, if_unused=False, if_empty=False)
            except aio_pika.exceptions.ChannelClosed:
                channel = await connection.channel()
        try:
            await channel.exchange_delete(f"{queue_name}-dlx", if_unused=False)
        except aio_pika.exceptions.ChannelClosed:
            pass
    finally:
        await connection.close()


async def _wait_for(
    predicate: Callable[[], Awaitable[bool]], *, timeout: float = 10
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("reliability scenario did not converge before its deadline")


class _HarnessConsumer(BaseConsumer):
    def __init__(
        self,
        queue_name: str,
        handler: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        prefetch_count: int = 1,
        interrupt_first_retry: bool = False,
    ) -> None:
        super().__init__(
            queue_name,
            prefetch_count=prefetch_count,
            retry_delays_seconds=[1],
            max_retry_attempts=1,
            retry_jitter_ratio=0,
        )
        self._handler = handler
        self._interrupt_first_retry = interrupt_first_retry
        self.retry_publish_interruptions = 0

    async def process_message(self, body: dict[str, Any]) -> None:
        await self._handler(body)

    async def _publish_retry(self, *args: Any, **kwargs: Any) -> None:
        if self._interrupt_first_retry:
            self._interrupt_first_retry = False
            self.retry_publish_interruptions += 1
            raise ConnectionError("synthetic retry publication interruption")
        await super()._publish_retry(*args, **kwargs)


async def _run_scenario(
    name: str, action: Callable[[], Awaitable[dict[str, Any]]]
) -> ScenarioResult:
    started = time.monotonic()
    evidence = await action()
    return ScenarioResult(
        name, "passed", round(time.monotonic() - started, 3), evidence
    )


async def _duplicate_delivery(run_id: str, queue_name: str) -> dict[str, Any]:
    scenario = "duplicate_delivery"

    async def handler(body: dict[str, Any]) -> None:
        if not await _claim(run_id, scenario, body["idempotency_key"]):
            raise DuplicateMessage("durable identity already claimed")

    consumer = _HarnessConsumer(queue_name, handler)
    await consumer.start()
    try:
        body = {"idempotency_key": f"{run_id}:duplicate"}
        await publish_message(queue_name, body, message_id=body["idempotency_key"])
        await publish_message(queue_name, body, message_id=body["idempotency_key"])

        async def complete() -> bool:
            return (await _counts(run_id, scenario)).get("claims") == 2

        await _wait_for(complete)
        counts = await _counts(run_id, scenario)
        if counts != {"claims": 2, "effects": 1, "identities": 1}:
            raise AssertionError(f"duplicate reconciliation failed: {counts}")
        return counts
    finally:
        await consumer.drain(3)


async def _retry_publication_interruption(
    run_id: str, queue_name: str
) -> dict[str, Any]:
    scenario = "retry_publication_interruption"
    calls = 0

    async def handler(body: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RetryableConsumerError("synthetic transient dependency")
        await _claim(run_id, scenario, body["idempotency_key"])

    consumer = _HarnessConsumer(queue_name, handler, interrupt_first_retry=True)
    await consumer.start()
    try:
        key = f"{run_id}:retry"
        await publish_message(queue_name, {"idempotency_key": key}, message_id=key)

        async def complete() -> bool:
            return (await _counts(run_id, scenario)).get("effects") == 1

        await _wait_for(complete, timeout=15)
        snapshot = await _queue_snapshot(queue_name)
        counts = await _counts(run_id, scenario)
        if calls != 3 or consumer.retry_publish_interruptions != 1:
            raise AssertionError(
                f"retry delivery was not preserved: calls={calls}, "
                f"interruptions={consumer.retry_publish_interruptions}"
            )
        if snapshot[f"{queue_name}-poison"]["ready"]:
            raise AssertionError(f"retry interruption poisoned work: {snapshot}")
        return {"handler_calls": calls, "counts": counts, "queues": snapshot}
    finally:
        await consumer.drain(3)


def _crash_after_claim(
    database_dsn: str,
    rabbitmq_url: str,
    redis_url: str,
    run_id: str,
    queue_name: str,
    key: str,
) -> None:
    async def crash() -> None:
        connection = await aio_pika.connect(rabbitmq_url)
        channel = await connection.channel()
        queue = await channel.declare_queue(queue_name, passive=True)
        message = await queue.get(fail=False, no_ack=False, timeout=10)
        if message is None:
            os._exit(31)
        database = await asyncpg.connect(database_dsn)
        await database.execute(
            """
            INSERT INTO bifrost_reliability_claims
                (run_id, scenario, idempotency_key)
            VALUES ($1, 'worker_death_after_claim', $2)
            ON CONFLICT (run_id, scenario, idempotency_key)
            DO UPDATE SET claims = bifrost_reliability_claims.claims + 1
            """,
            run_id,
            key,
        )
        await database.close()
        redis = Redis.from_url(redis_url, decode_responses=True)
        await redis.set(f"bifrost:reliability:{run_id}:claimed", "1", ex=60)
        await redis.aclose()
        os._exit(23)

    asyncio.run(crash())


async def _worker_death_after_claim(run_id: str, queue_name: str) -> dict[str, Any]:
    scenario = "worker_death_after_claim"
    key = f"{run_id}:crash"
    await publish_message(queue_name, {"idempotency_key": key}, message_id=key)
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_after_claim,
        args=(
            _database_dsn(),
            os.environ["BIFROST_RABBITMQ_URL"],
            os.environ["BIFROST_REDIS_URL"],
            run_id,
            queue_name,
            key,
        ),
    )
    process.start()
    redis = Redis.from_url(os.environ["BIFROST_REDIS_URL"], decode_responses=True)
    try:

        async def claimed() -> bool:
            return await redis.get(f"bifrost:reliability:{run_id}:claimed") == "1"

        await _wait_for(claimed)
        await asyncio.to_thread(process.join, 10)
        if process.exitcode != 23:
            raise AssertionError(
                f"synthetic worker exited unexpectedly: {process.exitcode}"
            )

        async def handler(body: dict[str, Any]) -> None:
            if not await _claim(run_id, scenario, body["idempotency_key"]):
                raise DuplicateMessage("recovered durable claim")

        consumer = _HarnessConsumer(queue_name, handler)
        await consumer.start()
        try:

            async def recovered() -> bool:
                return (await _counts(run_id, scenario)).get("claims") == 2

            await _wait_for(recovered)
            counts = await _counts(run_id, scenario)
            if counts != {"claims": 2, "effects": 1, "identities": 1}:
                raise AssertionError(f"crash recovery duplicated effects: {counts}")
            return {"worker_exit_code": process.exitcode, **counts}
        finally:
            await consumer.drain(3)
    finally:
        await redis.delete(f"bifrost:reliability:{run_id}:claimed")
        await redis.aclose()
        if process.is_alive():
            process.kill()
            process.join()


async def _admission_saturation(run_id: str, queue_name: str) -> dict[str, Any]:
    scenario = "admission_saturation"
    release = asyncio.Event()
    started = asyncio.Event()
    active = 0
    maximum_active = 0

    async def handler(body: dict[str, Any]) -> None:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        started.set()
        try:
            await release.wait()
            await _claim(run_id, scenario, body["idempotency_key"])
        finally:
            active -= 1

    consumer = _HarnessConsumer(queue_name, handler, prefetch_count=1)
    await consumer.start()
    try:
        for index in range(5):
            key = f"{run_id}:saturation:{index}"
            await publish_message(queue_name, {"idempotency_key": key}, message_id=key)
        await asyncio.wait_for(started.wait(), 5)
        await asyncio.sleep(0.2)
        saturated = await _queue_snapshot(queue_name)
        if saturated[queue_name]["ready"] < 4 or maximum_active != 1:
            raise AssertionError(f"prefetch did not bound admission: {saturated}")
        release.set()

        async def complete() -> bool:
            return (await _counts(run_id, scenario)).get("effects") == 5

        await _wait_for(complete)
        return {"maximum_active": maximum_active, "saturated_queues": saturated}
    finally:
        release.set()
        await consumer.drain(5)


async def _graceful_shutdown(run_id: str, queue_name: str) -> dict[str, Any]:
    scenario = "graceful_shutdown"
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(body: dict[str, Any]) -> None:
        started.set()
        await release.wait()
        await _claim(run_id, scenario, body["idempotency_key"])

    consumer = _HarnessConsumer(queue_name, handler)
    await consumer.start()
    key = f"{run_id}:drain"
    await publish_message(queue_name, {"idempotency_key": key}, message_id=key)
    await asyncio.wait_for(started.wait(), 5)
    drain = asyncio.create_task(consumer.drain(5))
    await asyncio.sleep(0.1)
    if drain.done():
        raise AssertionError("consumer drain did not wait for active work")
    release.set()
    await drain
    counts = await _counts(run_id, scenario)
    snapshot = await _queue_snapshot(queue_name)
    if counts.get("effects") != 1 or snapshot[queue_name]["ready"] != 0:
        raise AssertionError(
            f"graceful drain lost work: counts={counts} queues={snapshot}"
        )
    return {"counts": counts, "queues": snapshot}


async def run(output: Path) -> dict[str, Any]:
    run_id = uuid4().hex
    queue_prefix = f"bifrost-reliability-{run_id}"
    _require_safe_environment(queue_prefix)
    await _prepare_table()
    results: list[ScenarioResult] = []
    scenarios = (
        ("duplicate_delivery", _duplicate_delivery),
        ("retry_publication_interruption", _retry_publication_interruption),
        ("worker_death_after_claim", _worker_death_after_claim),
        ("admission_saturation", _admission_saturation),
        ("graceful_shutdown", _graceful_shutdown),
    )
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    failure: Exception | None = None
    try:
        for index, (name, scenario) in enumerate(scenarios):
            queue_name = f"{queue_prefix}-{index}"
            before[name] = await _queue_snapshot(queue_name)
            try:
                results.append(
                    await _run_scenario(
                        name, lambda s=scenario, q=queue_name: s(run_id, q)
                    )
                )
            except Exception as exc:  # noqa: BLE001 - report every scenario failure
                failure = exc
                results.append(
                    ScenarioResult(
                        name=name,
                        status="failed",
                        duration_seconds=0.0,
                        evidence={
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:500],
                        },
                    )
                )
            after[name] = await _queue_snapshot(queue_name)
            poison = after[name][f"{queue_name}-poison"]["ready"]
            if poison:
                failure = failure or AssertionError(
                    f"{name} unexpectedly produced poison={poison}"
                )
            if failure is not None:
                break
    finally:
        await rabbitmq.close()
        for index, _ in enumerate(scenarios):
            await _delete_topology(f"{queue_prefix}-{index}")
        await _delete_run(run_id)
        await _drop_table()
    report = {
        "schema": "bifrost.execution-reliability-gauntlet/v1",
        "run_id": run_id,
        "isolation": {
            "queue_prefix": queue_prefix,
            "database": _database_dsn().split("?", 1)[0].rsplit("/", 1)[-1],
            "production_fault_activation": False,
        },
        "before": before,
        "after": after,
        "results": [asdict(result) for result in results],
        "status": "failed" if failure is not None else "passed",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if failure is not None:
        raise failure
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.output))


if __name__ == "__main__":
    main()
