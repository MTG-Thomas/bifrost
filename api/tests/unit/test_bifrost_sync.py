from __future__ import annotations

import json

import pytest

from bifrost import _sync


class FakeRedis:
    def __init__(self, pending: dict[str, str] | None = None) -> None:
        self.pending = pending or {}
        self.deleted: list[str] = []

    async def hgetall(self, key: str) -> dict[str, str]:
        self.last_hgetall_key = key
        return self.pending

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _redis_provider(redis: FakeRedis):
    async def get_redis() -> FakeRedis:
        return redis

    return get_redis


@pytest.mark.asyncio
async def test_flush_pending_changes_returns_zero_when_redis_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(_sync, "get_shared_redis", _redis_provider(redis))
    monkeypatch.setattr(_sync, "pending_changes_key", lambda execution_id: f"pending:{execution_id}")

    assert await _sync.flush_pending_changes("exec-1", session=FakeSession()) == 0
    assert redis.deleted == []


@pytest.mark.asyncio
async def test_flush_pending_changes_sorts_valid_json_and_deletes_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis(
        {
            "a": json.dumps({"sequence": 2, "entity_type": "role"}),
            "b": "not-json",
            "c": json.dumps({"sequence": 1, "entity_type": "config"}),
        }
    )
    applied: list[str] = []
    session = FakeSession()
    monkeypatch.setattr(_sync, "get_shared_redis", _redis_provider(redis))
    monkeypatch.setattr(_sync, "pending_changes_key", lambda execution_id: f"pending:{execution_id}")

    async def apply_change(db, change):
        assert db is session
        applied.append(change["entity_type"])

    monkeypatch.setattr(_sync, "_apply_change", apply_change)

    assert await _sync.flush_pending_changes("exec-1", session=session) == 2
    assert applied == ["config", "role"]
    assert redis.deleted == ["pending:exec-1"]
    assert session.commits == 0


@pytest.mark.asyncio
async def test_flush_pending_changes_retries_and_raises_sync_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis({"a": json.dumps({"sequence": 1, "entity_type": "config"})})
    monkeypatch.setattr(_sync, "get_shared_redis", _redis_provider(redis))
    monkeypatch.setattr(_sync, "pending_changes_key", lambda execution_id: f"pending:{execution_id}")

    async def fail_apply(db, change):
        raise RuntimeError("db down")

    monkeypatch.setattr(_sync, "_apply_change", fail_apply)

    with pytest.raises(_sync.SyncError, match="Failed to flush: db down"):
        await _sync.flush_pending_changes("exec-1", session=FakeSession())

    assert redis.deleted == []


@pytest.mark.asyncio
async def test_flush_pending_changes_creates_session_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis({"a": json.dumps({"sequence": 1, "entity_type": "config"})})
    session = FakeSession()
    monkeypatch.setattr(_sync, "get_shared_redis", _redis_provider(redis))
    monkeypatch.setattr(_sync, "pending_changes_key", lambda execution_id: f"pending:{execution_id}")

    async def apply_change(db, change):
        assert db is session

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(_sync, "_apply_change", apply_change)
    monkeypatch.setattr(
        "src.core.database.get_session_factory",
        lambda: lambda: SessionContext(),
    )

    assert await _sync.flush_pending_changes("exec-1") == 1
    assert session.commits == 1
    assert redis.deleted == ["pending:exec-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entity_type", "target"),
    [
        ("config", "_apply_config_change"),
        ("role", "_apply_role_change"),
        ("organization", "_apply_org_change"),
        ("user_role", "_apply_user_role_change"),
        ("form_role", "_apply_form_role_change"),
    ],
)
async def test_apply_change_dispatches_by_entity_type(
    monkeypatch: pytest.MonkeyPatch,
    entity_type: str,
    target: str,
) -> None:
    calls: list[str] = []

    async def handler(db, change):
        calls.append(change["entity_type"])

    monkeypatch.setattr(_sync, target, handler)

    await _sync._apply_change(FakeSession(), {"entity_type": entity_type})

    assert calls == [entity_type]


@pytest.mark.asyncio
async def test_apply_change_ignores_unknown_entity_type() -> None:
    await _sync._apply_change(FakeSession(), {"entity_type": "unknown"})
