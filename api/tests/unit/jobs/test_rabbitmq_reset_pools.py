"""
Unit tests for RabbitMQConnection.reset_pools().

Regression coverage for the event-loop-pinning flake: the connection and
channel pools bind to whichever asyncio loop first touched them, so a test
running on a fresh function-scoped loop would reuse a pool pinned to a dead
loop and fail with ``RuntimeError: Event loop is closed`` on the next channel
open. ``reset_pools`` drops the references (without awaiting the dead loop) so
``init_pools`` rebuilds on the current loop.
"""

import pytest

from src.jobs.rabbitmq import RabbitMQConnection


@pytest.fixture
def conn():
    """The shared singleton, with its pools restored after each test.

    RabbitMQConnection is a singleton, so mutating its pools would leak into
    other tests in the same process; save and restore them.
    """
    c = RabbitMQConnection()
    saved = (c._connection_pool, c._channel_pool)
    try:
        yield c
    finally:
        c._connection_pool, c._channel_pool = saved


def test_reset_pools_clears_both_pools(conn):
    sentinel = object()
    conn._connection_pool = sentinel  # type: ignore[assignment]
    conn._channel_pool = sentinel  # type: ignore[assignment]

    conn.reset_pools()

    assert conn._connection_pool is None
    assert conn._channel_pool is None


def test_get_connection_requires_initialized_pool(conn):
    conn._connection_pool = None

    with pytest.raises(RuntimeError, match="Connection pool not initialized"):
        conn.get_connection()


def test_get_channel_requires_initialized_pool(conn):
    conn._channel_pool = None

    with pytest.raises(RuntimeError, match="Channel pool not initialized"):
        conn.get_channel()


@pytest.mark.asyncio
async def test_init_pools_noops_when_connection_pool_exists(conn, monkeypatch):
    sentinel = object()
    conn._connection_pool = sentinel  # type: ignore[assignment]
    conn._channel_pool = None

    async def fail_if_called():
        raise AssertionError("_init_pools should not run when pool exists")

    monkeypatch.setattr(conn, "_init_pools", fail_if_called)

    await conn.init_pools()

    assert conn._connection_pool is sentinel
    assert conn._channel_pool is None


@pytest.mark.asyncio
async def test_close_closes_existing_pools(conn):
    class _Pool:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    connection_pool = _Pool()
    channel_pool = _Pool()
    conn._connection_pool = connection_pool  # type: ignore[assignment]
    conn._channel_pool = channel_pool  # type: ignore[assignment]

    await conn.close()

    assert connection_pool.closed is True
    assert channel_pool.closed is True


def test_reset_pools_does_not_touch_stale_pool(conn):
    """reset_pools must be synchronous and must not call close().

    The whole point is to clear pools bound to an already-closed loop;
    awaiting close() on them would re-raise the very error we are clearing.
    A pool object that explodes on close() proves we never touch it.
    """

    class _Boom:
        def close(self):
            raise AssertionError("reset_pools must not call close() on the pool")

    conn._connection_pool = _Boom()  # type: ignore[assignment]
    conn._channel_pool = _Boom()  # type: ignore[assignment]

    # Synchronous call, no exception.
    conn.reset_pools()

    assert conn._connection_pool is None
    assert conn._channel_pool is None
