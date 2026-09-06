"""Shutdown must drain execution ownership beyond the Rabbit delivery task."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
from src.jobs.rabbitmq import BaseConsumer
from src.services.execution.process_pool import ProcessPoolManager


def make_consumer():
    consumer = WorkflowExecutionConsumer.__new__(WorkflowExecutionConsumer)
    BaseConsumer.__init__(consumer, queue_name="test-workflow-drain")
    consumer._pool = ProcessPoolManager()
    consumer._pool_started = True
    consumer._pool.stop = AsyncMock()
    return consumer


@pytest.mark.asyncio
async def test_drain_waits_for_routed_execution_and_result_persistence():
    consumer = make_consumer()
    pool = consumer._pool
    pool.processes["child"] = MagicMock()
    finish_result = asyncio.Event()
    result_started = asyncio.Event()

    async def persist_result():
        result_started.set()
        await finish_result.wait()

    result = asyncio.create_task(persist_result())
    pool._result_tasks.add(result)
    await result_started.wait()
    drain = asyncio.create_task(consumer.drain(deadline=1))
    await asyncio.sleep(0)
    pool.stop.assert_not_awaited()
    assert not pool._shutdown
    pool.processes.clear()
    await asyncio.sleep(0.06)
    pool.stop.assert_not_awaited()
    finish_result.set()
    await drain
    pool.stop.assert_awaited_once()
    assert result.done() and not result.cancelled()


@pytest.mark.asyncio
async def test_routing_handler_hands_off_before_pool_drain():
    consumer = make_consumer()
    release_route = asyncio.Event()
    routed = asyncio.Event()

    async def route():
        await release_route.wait()
        consumer._pool.processes["queued-claim"] = MagicMock()
        routed.set()

    handler = asyncio.create_task(route())
    consumer._inflight.add(handler)
    handler.add_done_callback(consumer._inflight.discard)
    drain = asyncio.create_task(consumer.drain(deadline=1))
    await asyncio.sleep(0)
    consumer._pool.stop.assert_not_awaited()
    release_route.set()
    await routed.wait()
    await asyncio.sleep(0.06)
    consumer._pool.stop.assert_not_awaited()
    consumer._pool.processes.clear()
    await drain
    consumer._pool.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_deadline_leaves_survivors_for_existing_durable_surrender():
    consumer = make_consumer()
    handle = MagicMock()
    consumer._pool.processes["child"] = handle
    await consumer.drain(deadline=0.01)
    consumer._pool.stop.assert_awaited_once()
    assert consumer._pool.processes["child"] is handle
    assert not consumer._pool._shutdown


@pytest.mark.asyncio
async def test_routing_and_pool_share_one_deadline():
    consumer = make_consumer()
    consumer._pool.drain = AsyncMock()
    handler = asyncio.create_task(asyncio.sleep(0.04))
    consumer._inflight.add(handler)
    handler.add_done_callback(consumer._inflight.discard)
    await consumer.drain(deadline=0.1)
    remaining = consumer._pool.drain.await_args.kwargs["deadline"]
    assert 0 <= remaining < 0.08


@pytest.mark.asyncio
async def test_cancelled_drain_still_stops_pool_with_owned_handles():
    consumer = make_consumer()
    handle = MagicMock()
    consumer._pool.processes["child"] = handle
    drain = asyncio.create_task(consumer.drain(deadline=1))
    await asyncio.sleep(0)
    drain.cancel()
    with pytest.raises(asyncio.CancelledError):
        await drain
    consumer._pool.stop.assert_awaited_once()
    assert consumer._pool.processes["child"] is handle


@pytest.mark.asyncio
async def test_stalled_handler_cancellation_cleanup_cannot_block_pool_surrender():
    consumer = make_consumer()
    handle = MagicMock()
    consumer._pool.processes["owned-child"] = handle
    handler_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def blocked_handler(_body):
        handler_started.set()
        await asyncio.Event().wait()

    async def stalled_retry(*_args, **_kwargs):
        cleanup_started.set()
        await release_cleanup.wait()

    consumer.process_message = AsyncMock(side_effect=blocked_handler)
    consumer._retry_or_poison = AsyncMock(side_effect=stalled_retry)
    message = MagicMock()
    message.body = b'{"execution_id": "owned-child"}'
    message.message_id = "drain-stalled-cleanup"
    message.ack = AsyncMock()
    message.nack = AsyncMock()
    await consumer._on_message(message)
    handler = next(iter(consumer._inflight))
    await handler_started.wait()
    drain = asyncio.create_task(consumer.drain(deadline=0.01))
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        completed, _ = await asyncio.wait({drain}, timeout=0.2)
        assert drain in completed, "Broker cancellation cleanup blocked pool surrender"
        await drain
        consumer._pool.stop.assert_awaited_once()
        assert consumer._pool.processes["owned-child"] is handle
        assert not handler.done()
    finally:
        release_cleanup.set()
        await asyncio.gather(drain, handler, return_exceptions=True)
