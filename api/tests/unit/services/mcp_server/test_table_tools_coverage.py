from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.services.mcp_server.tools import tables


def _context(*, admin: bool = False, org_id=None) -> SimpleNamespace:
    return SimpleNamespace(
        is_platform_admin=admin,
        org_id=org_id if org_id is not None else uuid4(),
        user_id=uuid4(),
        is_external=False,
    )


def _context_without_org(*, admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        is_platform_admin=admin,
        org_id=None,
        user_id=uuid4(),
        is_external=False,
    )


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar(self):
        return self._value


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Db:
    def __init__(self, results):
        self.results = list(results)
        self.added = []
        self.deleted = []
        self.committed = False

    async def execute(self, _stmt):
        if not self.results:
            raise AssertionError("unexpected execute call")
        return self.results.pop(0)

    def add(self, row):
        self.added.append(row)

    async def delete(self, row):
        self.deleted.append(row)

    async def commit(self):
        self.committed = True


class _RaisingDb:
    async def execute(self, _stmt):
        raise RuntimeError("database unavailable")


def _tool_db(db):
    @asynccontextmanager
    async def fake_get_tool_db(_context):
        yield db

    return fake_get_tool_db


def _table(**overrides):
    row = SimpleNamespace(
        id=uuid4(),
        name="Tickets",
        description="Ticket cache",
        organization_id=uuid4(),
        schema={"columns": [{"name": "ticket_id", "type": "string"}]},
        created_at=None,
        updated_at=None,
        created_by="user@example.test",
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


@pytest.mark.asyncio
async def test_table_schema_and_id_validation():
    with patch(
        "src.services.mcp_server.schema_utils.models_to_markdown",
        return_value="# Generated tables\n",
    ) as models_to_markdown:
        schema = await tables.get_table_schema(_context())

    assert "Table Schema Documentation" in models_to_markdown.call_args.args[1]
    assert "Column Types" in schema.structured_content["schema"]

    missing = await tables.get_table(_context(), table_id=None)
    bad = await tables.get_table(_context(), table_id="not-a-uuid")
    update_missing = await tables.update_table(_context(), table_id="")
    update_bad = await tables.update_table(_context(), table_id="not-a-uuid")
    delete_missing = await tables.delete_table(_context(), table_id="")
    delete_bad = await tables.delete_table(_context(), table_id="not-a-uuid")

    assert missing.structured_content["error"] == "table_id is required"
    assert "Invalid table_id format" in bad.structured_content["error"]
    assert update_missing.structured_content["error"] == "table_id is required"
    assert "Invalid table_id format" in update_bad.structured_content["error"]
    assert delete_missing.structured_content["error"] == "table_id is required"
    assert "Invalid table_id format" in delete_bad.structured_content["error"]


@pytest.mark.asyncio
async def test_list_and_get_tables_format_rows_and_report_errors():
    table_id = uuid4()
    table = _table(id=table_id, organization_id=None, name="Global")
    db = _Db([_RowsResult([table]), _ScalarResult(table), _ScalarResult(3)])

    with patch.object(tables, "get_tool_db", _tool_db(db)):
        listed = await tables.list_tables(_context(admin=True), scope="global")
        fetched = await tables.get_table(_context(admin=True), table_id=str(table_id))

    assert listed.structured_content["count"] == 1
    assert listed.structured_content["tables"][0]["scope"] == "global"
    assert fetched.structured_content["document_count"] == 3
    assert fetched.structured_content["columns"] == table.schema["columns"]

    with patch.object(tables, "get_tool_db", _tool_db(_RaisingDb())):
        failed = await tables.list_tables(_context())

    assert "Error listing tables" in failed.structured_content["error"]
    assert "database unavailable" in failed.structured_content["error"]


@pytest.mark.asyncio
async def test_create_table_validates_scope_and_org_before_db_access():
    org_id = uuid4()
    ctx = _context(admin=False, org_id=org_id)

    missing_name = await tables.create_table(ctx, name="")
    missing_org = await tables.create_table(_context_without_org(), name="Tickets")
    global_nonadmin = await tables.create_table(ctx, name="Global", scope="global")
    bad_org = await tables.create_table(
        _context(admin=True),
        name="Bad Org",
        organization_id="not-a-uuid",
    )
    other_org = await tables.create_table(
        ctx,
        name="Other Org",
        organization_id=str(uuid4()),
    )

    assert missing_name.structured_content["error"] == "name is required"
    assert "organization_id is required" in missing_org.structured_content["error"]
    assert "Only platform admins" in global_nonadmin.structured_content["error"]
    assert "Invalid organization_id format" in bad_org.structured_content["error"]
    assert "other organizations" in other_org.structured_content["error"]


@pytest.mark.asyncio
async def test_create_table_reports_duplicates_and_persists_new_table():
    org_id = uuid4()
    duplicate_db = _Db([_ScalarResult(_table(name="Tickets", organization_id=org_id))])

    with patch.object(tables, "get_tool_db", _tool_db(duplicate_db)):
        duplicate = await tables.create_table(
            _context(org_id=org_id),
            name="Tickets",
            organization_id=str(org_id),
        )

    create_db = _Db([_ScalarResult(None)])
    with patch.object(tables, "get_tool_db", _tool_db(create_db)):
        created = await tables.create_table(
            _context(org_id=org_id),
            name="Tickets",
            description="Ticket cache",
            organization_id=str(org_id),
            columns=[{"name": "ticket_id", "type": "string"}],
        )

    assert "already exists" in duplicate.structured_content["error"]
    assert create_db.committed is True
    assert create_db.added[0].name == "Tickets"
    assert create_db.added[0].schema == {
        "columns": [{"name": "ticket_id", "type": "string"}]
    }
    assert created.structured_content["success"] is True
    assert created.structured_content["scope"] == "organization"


@pytest.mark.asyncio
async def test_update_table_reports_missing_noop_and_applies_changes():
    table_id = uuid4()
    missing_db = _Db([_ScalarResult(None)])
    with patch.object(tables, "get_tool_db", _tool_db(missing_db)):
        missing = await tables.update_table(_context(), str(table_id), name="New")

    noop_table = _table(id=table_id)
    noop_db = _Db([_ScalarResult(noop_table)])
    with (
        patch.object(tables, "get_tool_db", _tool_db(noop_db)),
        patch("src.services.solutions.guard.is_solution_managed", return_value=False),
    ):
        noop = await tables.update_table(_context(), str(table_id))

    update_table = _table(id=table_id, schema=None)
    update_db = _Db([_ScalarResult(update_table)])
    new_org = uuid4()
    with (
        patch.object(tables, "get_tool_db", _tool_db(update_db)),
        patch("src.services.solutions.guard.is_solution_managed", return_value=False),
    ):
        updated = await tables.update_table(
            _context(admin=True),
            str(table_id),
            name="Updated",
            description="New description",
            scope="organization",
            organization_id=str(new_org),
            columns=[{"name": "status", "type": "string"}],
        )

    assert "Table not found" in missing.structured_content["error"]
    assert noop.structured_content["error"] == "No updates specified"
    assert update_db.committed is True
    assert update_table.name == "Updated"
    assert update_table.description == "New description"
    assert update_table.organization_id == new_org
    assert update_table.schema == {"columns": [{"name": "status", "type": "string"}]}
    assert updated.structured_content["updates"] == [
        "name",
        "description",
        "scope",
        "columns",
    ]


@pytest.mark.asyncio
async def test_update_and_delete_table_guard_permissions_and_delete_rows():
    table_id = uuid4()

    global_scope_db = _Db([_ScalarResult(_table(id=table_id))])
    with (
        patch.object(tables, "get_tool_db", _tool_db(global_scope_db)),
        patch("src.services.solutions.guard.is_solution_managed", return_value=False),
    ):
        global_scope = await tables.update_table(
            _context(admin=False),
            str(table_id),
            scope="global",
        )
    assert "Only platform admins" in global_scope.structured_content["error"]

    table = _table(id=table_id, name="Delete Me")
    db = _Db([_ScalarResult(table)])
    with (
        patch.object(tables, "get_tool_db", _tool_db(db)),
        patch("src.services.solutions.guard.is_solution_managed", return_value=False),
    ):
        deleted = await tables.delete_table(
            _context(admin=True),
            table_id=str(table_id),
        )

    assert db.deleted == [table]
    assert db.committed is True
    assert deleted.structured_content["success"] is True
    assert deleted.structured_content["name"] == "Delete Me"
