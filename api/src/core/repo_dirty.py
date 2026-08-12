"""Generation-fenced authoritative-workspace dirty state.

The marker is advanced before every platform-side ``_repo`` mutation.  A Git
closure may reconcile only the exact generation it snapshotted; a later write
therefore cannot be hidden by an older successful closure.

Timestamp-only values from older releases remain readable for operator safety,
but are intentionally ineligible for generation reconciliation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Awaitable, NamedTuple, cast
from uuid import uuid4

from src.core.cache.redis_client import get_redis

DIRTY_KEY = "bifrost:repo_dirty"

_MARK_DIRTY_SCRIPT = """
local current = redis.call('GET', KEYS[1])
local next = cjson.decode(ARGV[1])
if current then
  local ok, prior = pcall(cjson.decode, current)
  if ok and type(prior) == 'table' and prior['dirty_since'] then
    next['dirty_since'] = prior['dirty_since']
  elseif not ok then
    next['dirty_since'] = current
  end
end
local encoded = cjson.encode(next)
redis.call('SET', KEYS[1], encoded)
return encoded
"""

_RECONCILE_DIRTY_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then
  return 0
end
local ok, parsed = pcall(cjson.decode, current)
if not ok or type(parsed) ~= 'table' or parsed['generation'] ~= ARGV[1] then
  return 0
end
redis.call('DEL', KEYS[1])
return 1
"""


class RepoDirtyState(NamedTuple):
    generation: str | None
    dirty_since: str
    updated_at: str
    writer: str | None
    legacy: bool = False


def _decode(value: object) -> str | None:
    if isinstance(value, bytes):
        return value.decode()
    return value if isinstance(value, str) else None


def _parse(value: object) -> RepoDirtyState | None:
    raw = _decode(value)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return RepoDirtyState(None, raw, raw, None, True)
    if not isinstance(data, dict) or not isinstance(data.get("generation"), str):
        return RepoDirtyState(None, raw, raw, None, True)
    updated_at = str(data.get("updated_at") or data.get("dirty_since") or "")
    return RepoDirtyState(
        generation=data["generation"],
        dirty_since=str(data.get("dirty_since") or updated_at),
        updated_at=updated_at,
        writer=str(data["writer"]) if data.get("writer") is not None else None,
    )


async def mark_repo_dirty(*, writer: str | None = None) -> RepoDirtyState:
    """Advance dirty state and return the exact generation written."""
    timestamp = datetime.now(timezone.utc).isoformat()
    candidate = {
        "generation": uuid4().hex,
        "dirty_since": timestamp,
        "updated_at": timestamp,
        "writer": writer,
    }
    async with get_redis() as redis:
        encoded = await cast(
            Awaitable[object],
            redis.eval(
                _MARK_DIRTY_SCRIPT,
                1,
                DIRTY_KEY,
                json.dumps(candidate, separators=(",", ":")),
            ),
        )
    state = _parse(encoded)
    if state is None or state.generation != candidate["generation"]:
        raise RuntimeError("repository dirty generation was not persisted")
    return state


async def reconcile_repo_dirty(generation: str) -> bool:
    """Clear only a still-current structured dirty generation atomically."""
    async with get_redis() as redis:
        return bool(
            await cast(
                Awaitable[object],
                redis.eval(_RECONCILE_DIRTY_SCRIPT, 1, DIRTY_KEY, generation),
            )
        )


async def clear_repo_dirty() -> None:
    """Unconditionally clear after the legacy broad sync proves convergence.

    Transactional closures must call :func:`reconcile_repo_dirty` instead.
    """
    async with get_redis() as redis:
        await redis.delete(DIRTY_KEY)


async def get_repo_dirty_state() -> RepoDirtyState | None:
    async with get_redis() as redis:
        return _parse(await redis.get(DIRTY_KEY))


async def get_repo_dirty_since() -> str | None:
    """Compatibility projection used by older status clients."""
    state = await get_repo_dirty_state()
    return state.dirty_since if state else None
