from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core import entity_change_hook


class WatchedThing:
    def __init__(self, entity_id: str | None = "thing-1") -> None:
        self.id = entity_id


class WatchedByForeignKey:
    def __init__(self, integration_id: str | None = "integration-1") -> None:
        self.integration_id = integration_id


class UnwatchedThing:
    id = "ignored"


class SessionStub:
    def __init__(self, *, new=(), dirty=(), deleted=()) -> None:
        self.new = list(new)
        self.dirty = list(dirty)
        self.deleted = list(deleted)


@pytest.fixture(autouse=True)
def isolated_registry():
    original = dict(entity_change_hook._MODEL_REGISTRY)
    entity_change_hook._MODEL_REGISTRY.clear()
    entity_change_hook._MODEL_REGISTRY.update(
        {
            WatchedThing: ("things", "id"),
            WatchedByForeignKey: ("integrations", "integration_id"),
        }
    )
    yield
    entity_change_hook._MODEL_REGISTRY.clear()
    entity_change_hook._MODEL_REGISTRY.update(original)


def test_after_flush_collects_manifest_relevant_changes_only() -> None:
    session = SessionStub(
        new=[WatchedThing("new-1"), UnwatchedThing()],
        dirty=[WatchedByForeignKey("integration-1"), WatchedThing(None)],
        deleted=[WatchedThing("deleted-1")],
    )

    entity_change_hook._after_flush(session, None)

    assert getattr(session, entity_change_hook._PENDING_ATTR) == [
        ("things", "new-1", "add"),
        ("integrations", "integration-1", "update"),
        ("things", "deleted-1", "delete"),
    ]


def test_after_rollback_clears_collected_changes() -> None:
    session = SessionStub(new=[WatchedThing("new-1")])
    entity_change_hook._after_flush(session, None)

    entity_change_hook._after_rollback(session)

    assert getattr(session, entity_change_hook._PENDING_ATTR) == []


def test_after_commit_deduplicates_changes_and_schedules_publish_tasks() -> None:
    session = SessionStub()
    setattr(
        session,
        entity_change_hook._PENDING_ATTR,
        [
            ("things", "same-id", "add"),
            ("things", "same-id", "update"),
            ("integrations", "removed", "update"),
            ("integrations", "removed", "delete"),
        ],
    )
    loop = SimpleNamespace(create_task=lambda task: scheduled.append(task))
    scheduled = []
    user = SimpleNamespace(user_id="user-1", user_name="Operator")

    with (
        patch.object(entity_change_hook.asyncio, "get_running_loop", return_value=loop),
        patch("src.core.request_context.get_request_user", return_value=user),
        patch("src.core.request_context.get_request_session_id", return_value="session-1"),
        patch.object(entity_change_hook, "_publish_entity_change", AsyncMock(return_value=None)) as publish,
    ):
        entity_change_hook._after_commit(session)

    assert getattr(session, entity_change_hook._PENDING_ATTR) == []
    assert len(scheduled) == 2
    assert publish.call_args_list[0].kwargs == {
        "entity_type": "things",
        "entity_id": "same-id",
        "action": "update",
        "user_id": "user-1",
        "user_name": "Operator",
        "session_id": "session-1",
    }
    assert publish.call_args_list[1].kwargs["action"] == "delete"

    for task in scheduled:
        task.close()


def test_after_commit_clears_pending_when_no_event_loop_exists() -> None:
    session = SessionStub()
    pending = [("things", "same-id", "add")]
    setattr(session, entity_change_hook._PENDING_ATTR, pending)

    with patch.object(entity_change_hook.asyncio, "get_running_loop", side_effect=RuntimeError):
        entity_change_hook._after_commit(session)

    assert getattr(session, entity_change_hook._PENDING_ATTR) == []


@pytest.mark.asyncio
async def test_publish_delete_event_does_not_serialize_deleted_entity() -> None:
    with (
        patch.object(entity_change_hook, "_serialize_entity", AsyncMock()) as serialize,
        patch("src.core.pubsub.publish_file_activity", AsyncMock()) as publish,
    ):
        await entity_change_hook._publish_entity_change(
            entity_type="things",
            entity_id="deleted-1",
            action="delete",
            user_id="user-1",
            user_name="Operator",
            session_id="session-1",
        )

    serialize.assert_not_awaited()
    publish.assert_awaited_once_with(
        user_id="user-1",
        user_name="Operator",
        activity_type="entity_change",
        entity_type="things",
        entity_id="deleted-1",
        action="delete",
        session_id="session-1",
        data=None,
    )


@pytest.mark.asyncio
async def test_publish_update_event_includes_serialized_entity_data() -> None:
    with (
        patch.object(
            entity_change_hook,
            "_serialize_entity",
            AsyncMock(return_value={"name": "Thing"}),
        ) as serialize,
        patch("src.core.pubsub.publish_file_activity", AsyncMock()) as publish,
    ):
        await entity_change_hook._publish_entity_change(
            entity_type="things",
            entity_id="thing-1",
            action="update",
            user_id="user-1",
            user_name="Operator",
            session_id=None,
        )

    serialize.assert_awaited_once_with("things", "thing-1")
    publish.assert_awaited_once_with(
        user_id="user-1",
        user_name="Operator",
        activity_type="entity_change",
        entity_type="things",
        entity_id="thing-1",
        action="update",
        session_id=None,
        data={"name": "Thing"},
    )
