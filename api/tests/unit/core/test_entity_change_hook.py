from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
        self.executed = []

    def connection(self):
        session = self

        class Connection:
            def execute(self, statement):
                session.executed.append(statement)
                return SimpleNamespace(scalar_one=lambda: 7)

        return Connection()


class ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class DbResult:
    def __init__(self, *, scalar=None, scalars=()):
        self._scalar = scalar
        self._scalars = list(scalars)

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return ScalarRows(self._scalars)


class DbStub:
    def __init__(self, *results):
        self.results = list(results)

    async def execute(self, _statement):
        return self.results.pop(0)


class Serializable:
    def __init__(self, payload):
        self.payload = payload
        self.model_dump = MagicMock(return_value=payload)


def _db_context(db):
    @asynccontextmanager
    async def context():
        yield db

    return context


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
    setattr(session, entity_change_hook._CATALOG_PENDING_ATTR, True)

    entity_change_hook._after_rollback(session)

    assert getattr(session, entity_change_hook._PENDING_ATTR) == []
    assert not getattr(session, entity_change_hook._CATALOG_PENDING_ATTR)


def test_bulk_workflow_dml_marks_catalog_for_post_commit_publish() -> None:
    session = SessionStub()
    state = SimpleNamespace(
        is_insert=False,
        is_update=True,
        is_delete=False,
        statement=SimpleNamespace(table=SimpleNamespace(name="workflows")),
        session=session,
    )

    entity_change_hook._track_bulk_workflow_change(state)

    assert getattr(session, entity_change_hook._CATALOG_PENDING_ATTR)
    assert getattr(session, entity_change_hook._CATALOG_REVISION_ATTR) == 7
    assert len(session.executed) == 1


def test_before_flush_advances_revision_once_per_transaction() -> None:
    from src.models.orm.workflows import Workflow

    workflow = object.__new__(Workflow)
    session = SessionStub(new=[workflow])

    entity_change_hook._before_flush_workflow_catalog_revision(session, None, None)
    entity_change_hook._before_flush_workflow_catalog_revision(session, None, None)

    assert getattr(session, entity_change_hook._CATALOG_REVISION_ATTR) == 7
    assert len(session.executed) == 1


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


def test_after_commit_schedules_one_catalog_revision_for_workflow_changes() -> None:
    session = SessionStub()
    setattr(
        session,
        entity_change_hook._PENDING_ATTR,
        [
            ("workflows", "workflow-1", "add"),
            ("workflows", "workflow-2", "update"),
        ],
    )
    setattr(session, entity_change_hook._CATALOG_REVISION_ATTR, 11)
    scheduled = []
    loop = SimpleNamespace(create_task=lambda task: scheduled.append(task))

    with (
        patch.object(entity_change_hook.asyncio, "get_running_loop", return_value=loop),
        patch("src.core.request_context.get_request_user", return_value=None),
        patch("src.core.request_context.get_request_session_id", return_value=None),
        patch.object(entity_change_hook, "_publish_entity_change", AsyncMock()),
        patch.object(
            entity_change_hook,
            "_publish_workflow_catalog_change",
            AsyncMock(),
        ) as publish_catalog,
    ):
        entity_change_hook._after_commit(session)

    assert len(scheduled) == 3
    assert publish_catalog.call_count == 1
    publish_catalog.assert_called_once_with(11)
    for task in scheduled:
        task.close()


def test_after_commit_publishes_catalog_for_bulk_workflow_dml() -> None:
    session = SessionStub()
    setattr(session, entity_change_hook._CATALOG_PENDING_ATTR, True)
    setattr(session, entity_change_hook._CATALOG_REVISION_ATTR, 12)
    scheduled = []
    loop = SimpleNamespace(create_task=lambda task: scheduled.append(task))

    with (
        patch.object(entity_change_hook.asyncio, "get_running_loop", return_value=loop),
        patch("src.core.request_context.get_request_user", return_value=None),
        patch("src.core.request_context.get_request_session_id", return_value=None),
        patch.object(
            entity_change_hook,
            "_publish_workflow_catalog_change",
            AsyncMock(),
        ) as publish_catalog,
    ):
        entity_change_hook._after_commit(session)

    assert not getattr(session, entity_change_hook._CATALOG_PENDING_ATTR)
    assert len(scheduled) == 1
    assert publish_catalog.call_count == 1
    publish_catalog.assert_called_once_with(12)
    scheduled[0].close()


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


@pytest.mark.asyncio
async def test_serialize_table_returns_compact_manifest_payload() -> None:
    row = SimpleNamespace(id="table-1", name="Tickets")
    serialized = Serializable({"name": "Tickets"})

    with (
        patch(
            "src.core.database.get_db_context",
            _db_context(DbStub(DbResult(scalar=row))),
        ),
        patch(
            "src.services.manifest_generator.serialize_table",
            return_value=serialized,
        ) as serialize,
    ):
        result = await entity_change_hook._serialize_entity("tables", "table-1")

    assert result == {"name": "Tickets"}
    serialize.assert_called_once_with(row)
    serialized.model_dump.assert_called_once_with(
        mode="json",
        exclude_defaults=True,
        by_alias=True,
    )


@pytest.mark.asyncio
async def test_serialize_workflow_includes_role_ids() -> None:
    row = SimpleNamespace(id="workflow-1", name="Create Ticket")
    roles = [SimpleNamespace(role_id="role-a"), SimpleNamespace(role_id="role-b")]
    serialized = Serializable(
        {"name": "Create Ticket", "roles": ["role-a", "role-b"]}
    )

    with (
        patch(
            "src.core.database.get_db_context",
            _db_context(DbStub(DbResult(scalar=row), DbResult(scalars=roles))),
        ),
        patch(
            "src.services.manifest_generator.serialize_workflow",
            return_value=serialized,
        ) as serialize,
    ):
        result = await entity_change_hook._serialize_entity("workflows", "workflow-1")

    assert result == {"name": "Create Ticket", "roles": ["role-a", "role-b"]}
    serialize.assert_called_once_with(row, ["role-a", "role-b"])


@pytest.mark.asyncio
async def test_serialize_form_includes_role_ids() -> None:
    row = SimpleNamespace(id="form-1", name="Ticket Request")
    roles = [SimpleNamespace(role_id="requester")]
    serialized = Serializable({"name": "Ticket Request", "roles": ["requester"]})

    with (
        patch(
            "src.core.database.get_db_context",
            _db_context(DbStub(DbResult(scalar=row), DbResult(scalars=roles))),
        ),
        patch(
            "src.services.manifest_generator.serialize_form",
            return_value=serialized,
        ) as serialize,
    ):
        result = await entity_change_hook._serialize_entity("forms", "form-1")

    assert result == {"name": "Ticket Request", "roles": ["requester"]}
    serialize.assert_called_once_with(row, ["requester"])


@pytest.mark.asyncio
async def test_serialize_agent_includes_role_ids() -> None:
    row = SimpleNamespace(id="agent-1", name="Dispatcher")
    roles = [SimpleNamespace(role_id="operator")]
    serialized = Serializable({"name": "Dispatcher", "roles": ["operator"]})

    with (
        patch(
            "src.core.database.get_db_context",
            _db_context(DbStub(DbResult(scalar=row), DbResult(scalars=roles))),
        ),
        patch(
            "src.services.manifest_generator.serialize_agent",
            return_value=serialized,
        ) as serialize,
    ):
        result = await entity_change_hook._serialize_entity("agents", "agent-1")

    assert result == {"name": "Dispatcher", "roles": ["operator"]}
    serialize.assert_called_once_with(row, ["operator"])


@pytest.mark.asyncio
async def test_serialize_app_includes_role_ids() -> None:
    row = SimpleNamespace(id="app-1", name="Desk")
    roles = [SimpleNamespace(role_id="viewer")]
    serialized = Serializable({"name": "Desk", "roles": ["viewer"]})

    with (
        patch(
            "src.core.database.get_db_context",
            _db_context(DbStub(DbResult(scalar=row), DbResult(scalars=roles))),
        ),
        patch(
            "src.services.manifest_generator.serialize_app",
            return_value=serialized,
        ) as serialize,
    ):
        result = await entity_change_hook._serialize_entity("apps", "app-1")

    assert result == {"name": "Desk", "roles": ["viewer"]}
    serialize.assert_called_once_with(row, ["viewer"])


@pytest.mark.asyncio
async def test_serialize_integration_collects_schema_oauth_and_mappings() -> None:
    row = SimpleNamespace(id="integration-1", name="Halo")
    schemas = [SimpleNamespace(name="tenant")]
    oauth = SimpleNamespace(provider="halo")
    mappings = [SimpleNamespace(name="ticket")]
    serialized = Serializable({"name": "Halo"})

    with (
        patch(
            "src.core.database.get_db_context",
            _db_context(
                DbStub(
                    DbResult(scalar=row),
                    DbResult(scalars=schemas),
                    DbResult(scalar=oauth),
                    DbResult(scalars=mappings),
                )
            ),
        ),
        patch(
            "src.services.manifest_generator.serialize_integration",
            return_value=serialized,
        ) as serialize,
    ):
        result = await entity_change_hook._serialize_entity(
            "integrations",
            "integration-1",
        )

    assert result == {"name": "Halo"}
    serialize.assert_called_once_with(row, schemas, oauth, mappings)


@pytest.mark.asyncio
async def test_serialize_event_source_collects_related_rows() -> None:
    row = SimpleNamespace(id="event-1", name="Nightly")
    schedule = SimpleNamespace(cron="0 0 * * *")
    webhook = SimpleNamespace(path="/hook")
    subscriptions = [SimpleNamespace(workflow_id="workflow-1")]
    serialized = Serializable({"name": "Nightly"})

    with (
        patch(
            "src.core.database.get_db_context",
            _db_context(
                DbStub(
                    DbResult(scalar=row),
                    DbResult(scalar=schedule),
                    DbResult(scalar=webhook),
                    DbResult(scalars=subscriptions),
                )
            ),
        ),
        patch(
            "src.services.manifest_generator.serialize_event_source",
            return_value=serialized,
        ) as serialize,
    ):
        result = await entity_change_hook._serialize_entity("events", "event-1")

    assert result == {"name": "Nightly"}
    serialize.assert_called_once_with(row, schedule, webhook, subscriptions)


@pytest.mark.asyncio
async def test_serialize_config_returns_none_when_row_is_missing() -> None:
    with patch(
        "src.core.database.get_db_context",
        _db_context(DbStub(DbResult(scalar=None))),
    ):
        assert await entity_change_hook._serialize_entity("configs", "missing") is None


@pytest.mark.asyncio
async def test_serialize_organization_uses_manifest_serializer() -> None:
    row = SimpleNamespace(id="org-1", name="Acme")
    serialized = Serializable({"name": "Acme"})

    with (
        patch(
            "src.core.database.get_db_context",
            _db_context(DbStub(DbResult(scalar=row))),
        ),
        patch(
            "src.services.manifest_generator.serialize_organization",
            return_value=serialized,
        ) as serialize,
    ):
        result = await entity_change_hook._serialize_entity("organizations", "org-1")

    assert result == {"name": "Acme"}
    serialize.assert_called_once_with(row)


@pytest.mark.asyncio
async def test_serialize_role_uses_manifest_serializer() -> None:
    row = SimpleNamespace(id="role-1", name="Dispatcher")
    serialized = Serializable({"name": "Dispatcher"})

    with (
        patch(
            "src.core.database.get_db_context",
            _db_context(DbStub(DbResult(scalar=row))),
        ),
        patch(
            "src.services.manifest_generator.serialize_role",
            return_value=serialized,
        ) as serialize,
    ):
        result = await entity_change_hook._serialize_entity("roles", "role-1")

    assert result == {"name": "Dispatcher"}
    serialize.assert_called_once_with(row)


@pytest.mark.asyncio
async def test_serialize_entity_logs_and_returns_none_on_query_failure() -> None:
    class BrokenDb:
        async def execute(self, _statement):
            raise RuntimeError("database unavailable")

    with (
        patch("src.core.database.get_db_context", _db_context(BrokenDb())),
        patch.object(entity_change_hook.logger, "warning") as warning,
    ):
        result = await entity_change_hook._serialize_entity("tables", "table-1")

    assert result is None
    assert "Failed to serialize tables/table-1" in warning.call_args.args[0]


def test_after_commit_returns_without_pending_changes() -> None:
    session = SessionStub()

    with patch.object(entity_change_hook.asyncio, "get_running_loop") as get_loop:
        entity_change_hook._after_commit(session)

    get_loop.assert_not_called()


def test_register_entity_change_hooks_registers_session_listeners() -> None:
    entity_change_hook._MODEL_REGISTRY[WatchedThing] = ("things", "id")

    with (
        patch.object(entity_change_hook.event, "listen") as listen,
        patch.object(entity_change_hook.logger, "info") as info,
    ):
        entity_change_hook.register_entity_change_hooks()

    assert [call.args[1:] for call in listen.call_args_list] == [
        ("before_flush", entity_change_hook._before_flush_workflow_catalog_revision),
        ("after_flush", entity_change_hook._after_flush),
        ("do_orm_execute", entity_change_hook._track_bulk_workflow_change),
        ("after_commit", entity_change_hook._after_commit),
        ("after_rollback", entity_change_hook._after_rollback),
    ]
    info.assert_called_once()
