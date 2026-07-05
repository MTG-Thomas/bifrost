from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.core import rate_limit


class _Pipe:
    def __init__(self) -> None:
        self.calls = []
        self.executed = False

    def incr(self, key: str) -> None:
        self.calls.append(("incr", key))

    def expire(self, key: str, ttl: int) -> None:
        self.calls.append(("expire", key, ttl))

    async def execute(self) -> None:
        self.executed = True


class _Redis:
    def __init__(self, *, current=1, ttl=42, stored=None) -> None:
        self.current = current
        self.ttl_value = ttl
        self.stored = stored
        self.expire_calls = []
        self.get_calls = []
        self.pipe = _Pipe()

    async def incr(self, key: str) -> int:
        return self.current

    async def expire(self, key: str, ttl: int) -> None:
        self.expire_calls.append((key, ttl))

    async def ttl(self, key: str) -> int:
        return self.ttl_value

    async def get(self, key: str):
        self.get_calls.append(key)
        return self.stored

    def pipeline(self) -> _Pipe:
        return self.pipe


def _testing(value: bool):
    return SimpleNamespace(is_testing=value)


def _redis_factory(redis: _Redis):
    async def get_shared_redis():
        return redis

    return get_shared_redis


@pytest.mark.asyncio
async def test_rate_limiter_skips_in_testing_unless_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_redis():
        raise AssertionError("testing bypass should not touch Redis")

    monkeypatch.setattr(rate_limit, "get_settings", lambda: _testing(True))
    monkeypatch.setattr(rate_limit, "get_shared_redis", fail_redis)

    await rate_limit.RateLimiter(max_requests=1).check("login", "ip")


@pytest.mark.asyncio
async def test_rate_limiter_first_request_sets_window_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _Redis(current=1)
    monkeypatch.setattr(rate_limit, "get_settings", lambda: _testing(True))
    monkeypatch.setattr(rate_limit, "get_shared_redis", _redis_factory(redis))

    await rate_limit.RateLimiter(max_requests=2, window_seconds=30).check(
        "login",
        "ip",
        force=True,
    )

    assert redis.expire_calls == [("bifrost:ratelimit:login:ip", 30)]


@pytest.mark.asyncio
async def test_rate_limiter_raises_429_with_retry_after_and_hit_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _Redis(current=3, ttl=17)
    monkeypatch.setattr(rate_limit, "get_settings", lambda: _testing(False))
    monkeypatch.setattr(rate_limit, "get_shared_redis", _redis_factory(redis))

    with pytest.raises(HTTPException) as exc:
        await rate_limit.RateLimiter(max_requests=2).check("webhook", "source-1")

    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "17"}
    assert redis.pipe.calls == [
        ("incr", "bifrost:rate_limit_hits:source-1"),
        ("expire", "bifrost:rate_limit_hits:source-1", 86400),
    ]
    assert redis.pipe.executed is True


@pytest.mark.asyncio
async def test_get_remaining_returns_full_limit_for_missing_counter_and_clamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = _Redis(stored=None)
    monkeypatch.setattr(rate_limit, "get_shared_redis", _redis_factory(missing))
    limiter = rate_limit.RateLimiter(max_requests=5)

    assert await limiter.get_remaining("login", "ip") == 5

    over_limit = _Redis(stored="8")
    monkeypatch.setattr(rate_limit, "get_shared_redis", _redis_factory(over_limit))

    assert await limiter.get_remaining("login", "ip") == 0


def test_get_client_ip_prefers_forwarded_for_then_client_then_unknown() -> None:
    assert (
        rate_limit.get_client_ip(
            SimpleNamespace(
                headers={"X-Forwarded-For": "203.0.113.5, 10.0.0.1"},
                client=SimpleNamespace(host="10.0.0.2"),
            )
        )
        == "203.0.113.5"
    )
    assert (
        rate_limit.get_client_ip(
            SimpleNamespace(headers={}, client=SimpleNamespace(host="10.0.0.2"))
        )
        == "10.0.0.2"
    )
    assert rate_limit.get_client_ip(SimpleNamespace(headers={}, client=None)) == "unknown"


@pytest.mark.asyncio
async def test_rate_limit_dependency_uses_client_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def check(self, endpoint: str, identifier: str) -> None:
        calls.append((endpoint, identifier))

    monkeypatch.setattr(rate_limit.RateLimiter, "check", check)

    dependency = rate_limit.rate_limit("login", max_requests=4, window_seconds=20)
    await dependency(
        SimpleNamespace(
            headers={"X-Forwarded-For": "198.51.100.9"},
            client=SimpleNamespace(host="10.0.0.2"),
        )
    )

    assert calls == [("login", "198.51.100.9")]
