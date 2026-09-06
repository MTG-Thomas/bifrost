"""Atomic admission for requests sharing an integration configuration.

This is cooperative request admission, not a sandbox for vendor effects.
A lease survives caller cancellation so uncertain requests retain their slot.
"""
from __future__ import annotations

from uuid import UUID

from src.core.cache import get_redis

LEASE_SECONDS = 60
MAX_REQUEST_SECONDS = 30
SETTING = "request_concurrency_limit"

# Use Redis time, and never extend an existing token on HTTP retry. The owner
# token is scoped to the authenticated principal by the router.
_ACQUIRE = """
local now_parts = redis.call('TIME')
local now = tonumber(now_parts[1]) * 1000 + tonumber(now_parts[2]) / 1000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
local existing = redis.call('ZSCORE', KEYS[1], ARGV[1])
if existing then return {1, math.floor(tonumber(existing) - now)} end
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return {0, 0} end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[3]), ARGV[1])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[3]))
return {1, tonumber(ARGV[3])}
"""
_RELEASE = "return redis.call('ZREM', KEYS[1], ARGV[1])"


def validate_limit(value: object) -> int | None:
    """An absent setting disables admission; invalid explicit settings fail closed."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError(f"{SETTING} must be an integer between 1 and 100")
    return value


async def acquire(integration_id: UUID, owner: str, limit: int) -> tuple[bool, float]:
    key = f"bifrost:integration-request-slots:{integration_id}"
    async with get_redis() as redis:
        result = await redis.eval(_ACQUIRE, 1, key, owner, limit, LEASE_SECONDS * 1000)
    return bool(result[0]), float(result[1]) / 1000


async def release(integration_id: UUID, owner: str) -> None:
    async with get_redis() as redis:
        await redis.eval(_RELEASE, 1, f"bifrost:integration-request-slots:{integration_id}", owner)
