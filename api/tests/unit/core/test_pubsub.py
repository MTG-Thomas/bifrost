from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from src.core import pubsub


class RecorderManager:
    def __init__(self) -> None:
        self.broadcasts: list[tuple[str, dict[str, Any]]] = []
        self.redis_messages: list[tuple[str, dict[str, Any]]] = []

    async def broadcast(self, channel: str, message: dict[str, Any]) -> None:
        self.broadcasts.append((channel, message))

    async def _publish_to_redis(self, channel: str, message: dict[str, Any]) -> bool:
        self.redis_messages.append((channel, message))
        return True


class RecorderPublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        self.messages.append((channel, payload))


class FakeSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[str] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, message: str) -> None:
        if self.fail:
            raise RuntimeError("closed")
        self.sent.append(message)


class RedisContext:
    def __init__(self, redis: Any) -> None:
        self.redis = redis

    async def __aenter__(self) -> Any:
        return self.redis

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> RecorderManager:
    manager = RecorderManager()
    monkeypatch.setattr(pubsub, "manager", manager)
    return manager


@pytest.mark.asyncio
async def test_connection_manager_broadcast_falls_back_to_local_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = pubsub.ConnectionManager()
    live = FakeSocket()
    dead = FakeSocket(fail=True)
    manager.connections["execution:1"] = {live, dead}

    async def publish_failed(_channel: str, _message: dict[str, Any]) -> bool:
        return False

    monkeypatch.setattr(manager, "_publish_to_redis", publish_failed)

    await manager.broadcast("execution:1", {"type": "event", "ok": True})

    assert json.loads(live.sent[0]) == {"type": "event", "ok": True}
    assert manager.connections["execution:1"] == {live}


@pytest.mark.asyncio
async def test_connection_manager_uses_table_and_file_dispatchers() -> None:
    manager = pubsub.ConnectionManager()
    table_calls: list[tuple[str, dict[str, Any]]] = []
    file_calls: list[tuple[str, dict[str, Any]]] = []

    table_socket = FakeSocket()
    file_socket = FakeSocket()

    async def table_dispatcher(channel: str, message: dict[str, Any]) -> None:
        table_calls.append((channel, message))

    async def file_dispatcher(channel: str, message: dict[str, Any]) -> None:
        file_calls.append((channel, message))

    cast(Any, table_socket)._table_dispatcher = table_dispatcher
    cast(Any, file_socket)._file_dispatcher = file_dispatcher
    manager.connections["table:t1"] = {table_socket}
    manager.connections["files:workspace:GLOBAL"] = {file_socket}

    await manager._send_local("table:t1", {"type": "document_change"})
    await manager._send_local("files:workspace:GLOBAL", {"type": "file_change"})

    assert table_calls == [("table:t1", {"type": "document_change"})]
    assert file_calls == [("files:workspace:GLOBAL", {"type": "file_change"})]
    assert table_socket.sent == []
    assert file_socket.sent == []


@pytest.mark.asyncio
async def test_connection_manager_publish_to_redis_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = pubsub.ConnectionManager()
    published: list[tuple[str, str]] = []

    class Redis:
        async def publish(self, channel: str, message: str) -> None:
            published.append((channel, message))

    monkeypatch.setattr(pubsub, "get_redis", lambda: RedisContext(Redis()))

    assert await manager._publish_to_redis("execution:1", {"ok": True}) is True
    assert published == [("bifrost:execution:1", '{"ok": true}')]

    def fail_get_redis() -> RedisContext:
        raise RuntimeError("redis down")

    monkeypatch.setattr(pubsub, "get_redis", fail_get_redis)
    assert await manager._publish_to_redis("execution:1", {"ok": True}) is False


@pytest.mark.asyncio
async def test_connection_manager_init_redis_replaces_listener_and_routes_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[str, dict[str, Any]]] = []
    stopped: list[str] = []
    started: list[dict[str, Any]] = []

    class OldListener:
        async def stop(self) -> None:
            stopped.append("old")

    class Listener:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def start(self) -> None:
            started.append(self.kwargs)
            await self.kwargs["on_message"]("bifrost:user:1", {"type": "notification"})

        async def stop(self) -> None:
            stopped.append("new")

    async def send_local(channel: str, message: dict[str, Any]) -> None:
        sent.append((channel, message))

    manager = pubsub.ConnectionManager(_pubsub_listener=cast(Any, OldListener()))
    monkeypatch.setattr(pubsub, "get_settings", lambda: SimpleNamespace(redis_url="redis://test"))
    monkeypatch.setattr(pubsub, "ResilientPubSubListener", Listener)
    monkeypatch.setattr(manager, "_send_local", send_local)

    await manager._init_redis()
    await manager.close()

    assert stopped == ["old", "new"]
    assert started[0]["redis_url"] == "redis://test"
    assert started[0]["patterns"] == ["bifrost:*"]
    assert sent == [("user:1", {"type": "notification"})]


@pytest.mark.asyncio
async def test_basic_publishers_emit_expected_channels(recorder: RecorderManager) -> None:
    execution_id = uuid4()
    user_id = uuid4()

    await pubsub.publish_execution_update(execution_id, "Success", {"result": 1})
    await pubsub.publish_execution_log(execution_id, "info", "done", {"line": 1})
    await pubsub.publish_user_notification(user_id, "success", "Ready", "Finished", {"href": "/x"})
    await pubsub.publish_system_event("maintenance", {"window": "tonight"})
    await pubsub.publish_local_runner_state_update(user_id, {"connected": True})
    await pubsub.publish_cli_session_update(user_id, "sess-1", None)

    channels = [channel for channel, _ in recorder.broadcasts]
    assert channels == [
        f"execution:{execution_id}",
        f"execution:{execution_id}",
        f"user:{user_id}",
        "system",
        f"local-runner:{user_id}",
        "cli-session:sess-1",
        f"cli-sessions:{user_id}",
    ]
    assert recorder.broadcasts[0][1]["status"] == "Success"
    assert recorder.broadcasts[1][1]["data"] == {"line": 1}
    assert recorder.broadcasts[2][1]["href"] == "/x"
    assert recorder.broadcasts[3][1]["eventType"] == "maintenance"


@pytest.mark.asyncio
async def test_history_and_agent_run_publish_to_all_relevant_channels(recorder: RecorderManager) -> None:
    execution_id = uuid4()
    user_id = uuid4()
    org_id = uuid4()
    started_at = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    completed_at = datetime(2026, 7, 4, 12, 1, tzinfo=timezone.utc)

    await pubsub.publish_history_update(
        execution_id,
        "Success",
        user_id,
        "User Name",
        "Workflow",
        org_id=org_id,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=60000,
    )

    run = SimpleNamespace(
        id=uuid4(),
        agent_id=uuid4(),
        org_id=org_id,
        status="completed",
        trigger_type="manual",
        iterations_used=None,
        tokens_used=12,
        duration_ms=1000,
        error=None,
        started_at=started_at,
        completed_at=completed_at,
        confidence="0.75",
        summary_status="done",
        summary_error=None,
        asked="question",
        did="answer",
    )
    await pubsub.publish_agent_run_update(cast(Any, run), "Helper")

    channels = [channel for channel, _ in recorder.broadcasts]
    assert f"history:user:{user_id}" in channels
    assert "history:GLOBAL" in channels
    assert f"agent-run:{run.id}" in channels
    assert f"agent-runs:org:{org_id}" in channels
    assert "agent-runs:all" in channels

    history_payload = recorder.broadcasts[0][1]
    assert history_payload["started_at"] == started_at.isoformat()
    assert history_payload["completed_at"] == completed_at.isoformat()

    agent_payload = next(payload for channel, payload in recorder.broadcasts if channel == f"agent-run:{run.id}")
    assert agent_payload["iterations_used"] == 0
    assert agent_payload["confidence"] == 0.75


@pytest.mark.asyncio
async def test_app_worker_pool_and_file_activity_publishers(recorder: RecorderManager) -> None:
    await pubsub.publish_summary_backfill_update("job-1", {"status": "running"})
    await pubsub.publish_agent_run_step("run-1", {"name": "think"})
    await pubsub.publish_app_draft_update("app-1", "user-1", "User", "page", "page-1")
    await pubsub.publish_app_code_file_update(
        "app-1",
        "user-1",
        "User",
        "pages/index",
        source="export default 1",
        bundle={"entry": "main.js"},
    )
    await pubsub.publish_app_published("app-1", "user-1", "User", "version-2")
    await pubsub.publish_worker_event({"type": "worker_online", "worker_id": "w1"})
    await pubsub.publish_pool_config_changed("w1", 1, 2, 2, 4)
    await pubsub.publish_pool_scaling("w1", "scale_up", 2)
    await pubsub.publish_pool_progress("w1", "scale_up", 1, 2, "spawning")
    await pubsub.publish_file_activity(
        "user-1",
        "User",
        "file_push",
        paths=["workflows/a.py"],
        session_id="sess-1",
        entity_type="workflow",
        entity_id="wf-1",
        action="write",
        data={"count": 1},
    )

    channels = [channel for channel, _ in recorder.broadcasts]
    assert channels == [
        "summary-backfill:job-1",
        "agent-run:run-1",
        "app:draft:app-1",
        "app:draft:app-1",
        "app:live:app-1",
        "platform_workers",
        "platform_workers",
        "platform_workers",
        "platform_workers",
        "file-activity",
    ]
    assert recorder.broadcasts[3][1]["bundle"] == {"entry": "main.js"}
    assert recorder.broadcasts[-1][1]["paths"] == ["workflows/a.py"]
    assert recorder.broadcasts[-1][1]["data"] == {"count": 1}


@pytest.mark.asyncio
async def test_worker_heartbeat_stores_when_worker_id_present(
    recorder: RecorderManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: list[tuple[str, int, str]] = []

    class RedisClient:
        async def setex(self, key: str, ttl: int, value: str) -> None:
            stored.append((key, ttl, value))

    monkeypatch.setattr("src.core.redis_client.get_redis_client", lambda: RedisClient())

    await pubsub.publish_worker_heartbeat({"worker_id": "worker-1", "status": "online"})
    await pubsub.publish_worker_heartbeat({"status": "missing-worker-id"})

    assert recorder.broadcasts == [
        ("platform_workers", {"worker_id": "worker-1", "status": "online"}),
        ("platform_workers", {"status": "missing-worker-id"}),
    ]
    assert stored[0][0] == "bifrost:pool:worker-1:heartbeat"
    assert stored[0][1] == 60
    assert json.loads(stored[0][2])["status"] == "online"


@pytest.mark.asyncio
async def test_scheduler_redis_publishers_and_git_completion(
    recorder: RecorderManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: list[tuple[str, int, str]] = []

    class RedisClient:
        async def setex(self, key: str, ttl: int, value: str) -> None:
            stored.append((key, ttl, value))

    monkeypatch.setattr("src.core.redis_client.get_redis_client", lambda: RedisClient())

    await pubsub.publish_git_operation("job-1", "org-1", "user-1", "u@example.com", "git_fetch", branch="main")
    await pubsub.publish_reimport_request("job-2")
    await pubsub.publish_embedding_reindex_request("note-1")
    await pubsub.publish_git_progress("job-1", "Fetching", current=1, total=3)
    await pubsub.publish_git_op_completed(
        "job-1",
        "success",
        "sync_execute",
        data={"ok": True},
        preview={"changes": []},
        pulled=1,
        pushed=2,
        commit_sha="abc123",
        conflicts=[],
    )

    assert recorder.redis_messages[0] == (
        "scheduler:git-op",
        {
            "type": "git_fetch",
            "jobId": "job-1",
            "orgId": "org-1",
            "userId": "user-1",
            "userEmail": "u@example.com",
            "branch": "main",
        },
    )
    assert recorder.redis_messages[1] == ("scheduler:reimport", {"action": "reimport", "job_id": "job-2"})
    assert recorder.redis_messages[2] == (
        "scheduler:embedding-reindex",
        {"action": "embedding_reindex", "notification_id": "note-1"},
    )
    assert recorder.broadcasts[-2][0] == "git:job-1"
    assert recorder.broadcasts[-1][1]["commit_sha"] == "abc123"
    assert stored[0][0] == "bifrost:job:job-1"
    assert stored[0][1] == 300
    assert json.loads(stored[0][2])["pushed"] == 2


@pytest.mark.asyncio
async def test_table_and_file_publishers(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = RecorderPublisher()
    monkeypatch.setattr(pubsub, "publisher", recorder)

    await pubsub.publish_document_change("table-1", "update", {"id": 1}, {"id": 1, "name": "new"})
    await pubsub.publish_policy_changed("table-1")
    await pubsub.publish_file_change(location="workspace", scope=None, path="a.py", action="write")
    await pubsub.publish_file_policy_changed(location="workspace", scope="global", path="policies/")
    await pubsub.publish_file_change(location="workspace", scope="org-1", path="b.py", action="delete")

    assert recorder.messages == [
        (
            "table:table-1",
            {"type": "document_change", "table_id": "table-1", "action": "update", "old_row": {"id": 1}, "new_row": {"id": 1, "name": "new"}},
        ),
        ("table:table-1", {"type": "policy_changed", "table_id": "table-1"}),
        (
            "files:workspace:GLOBAL",
            {"type": "file_change", "location": "workspace", "scope": None, "path": "a.py", "action": "write"},
        ),
        (
            "files:workspace:GLOBAL",
            {"type": "file_policy_changed", "location": "workspace", "scope": "global", "path": "policies/"},
        ),
        (
            "files:workspace:org-1",
            {"type": "file_change", "location": "workspace", "scope": "org-1", "path": "b.py", "action": "delete"},
        ),
    ]
