from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.services.mcp_server.tools import organizations


def _context(*, admin: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        is_platform_admin=admin,
        user_email="admin@example.test" if admin else "user@example.test",
    )


def _org(**overrides):
    row = SimpleNamespace(
        id=uuid4(),
        name="Midtown",
        domain="midtown",
        is_active=True,
        settings={"theme": "default"},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        created_by="admin@example.test",
        updated_at=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Db:
    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.committed = False

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("unexpected execute call")
        return self._results.pop(0)

    def add(self, row):
        self.added.append(row)

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


@pytest.mark.asyncio
async def test_list_organizations_formats_rows_and_reports_errors():
    org = _org(name="Alpha", domain="alpha", is_active=False)
    db = _Db([_RowsResult([org])])

    with patch.object(organizations, "get_tool_db", _tool_db(db)):
        result = await organizations.list_organizations(_context())

    assert result.structured_content["count"] == 1
    assert result.structured_content["organizations"] == [
        {
            "id": str(org.id),
            "name": "Alpha",
            "domain": "alpha",
            "is_active": False,
        }
    ]

    with patch.object(organizations, "get_tool_db", _tool_db(_RaisingDb())):
        failed = await organizations.list_organizations(_context())

    assert "Error listing organizations" in failed.structured_content["error"]
    assert "database unavailable" in failed.structured_content["error"]


@pytest.mark.asyncio
async def test_get_organization_validates_identifiers_and_formats_details():
    missing_lookup = await organizations.get_organization(_context())
    assert "Either organization_id or domain is required" in (
        missing_lookup.structured_content["error"]
    )

    db = _Db([_ScalarResult(_org())])
    with patch.object(organizations, "get_tool_db", _tool_db(db)):
        by_domain = await organizations.get_organization(_context(), domain="midtown")

    assert by_domain.structured_content["name"] == "Midtown"
    assert by_domain.structured_content["settings"] == {"theme": "default"}
    assert by_domain.structured_content["created_at"] == "2026-01-01T00:00:00+00:00"
    assert by_domain.structured_content["updated_at"] is None

    org_id = uuid4()
    db = _Db([_ScalarResult(_org(id=org_id, updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc)))])
    with patch.object(organizations, "get_tool_db", _tool_db(db)):
        by_id = await organizations.get_organization(_context(), organization_id=str(org_id))

    assert by_id.structured_content["id"] == str(org_id)
    assert by_id.structured_content["updated_at"] == "2026-01-02T00:00:00+00:00"

    db = _Db([_ScalarResult(None)])
    with patch.object(organizations, "get_tool_db", _tool_db(db)):
        not_found = await organizations.get_organization(_context(), domain="missing")

    assert "Organization not found: missing" in not_found.structured_content["error"]

    db = _Db([])
    with patch.object(organizations, "get_tool_db", _tool_db(db)):
        bad_id = await organizations.get_organization(
            _context(),
            organization_id="not-a-uuid",
        )

    assert "Invalid organization_id format" in bad_id.structured_content["error"]


@pytest.mark.asyncio
async def test_create_organization_validates_and_generates_domain():
    missing_name = await organizations.create_organization(_context(), "")
    long_name = await organizations.create_organization(_context(), "x" * 256)
    long_domain = await organizations.create_organization(
        _context(),
        "Valid",
        domain="x" * 256,
    )

    assert missing_name.structured_content["error"] == "name is required"
    assert "255 characters" in long_name.structured_content["error"]
    assert "255 characters" in long_domain.structured_content["error"]

    db = _Db([_ScalarResult(_org(domain="existing"))])
    with patch.object(organizations, "get_tool_db", _tool_db(db)):
        duplicate = await organizations.create_organization(
            _context(),
            "Existing",
            domain="existing",
        )

    assert "already exists" in duplicate.structured_content["error"]

    db = _Db([_ScalarResult(None)])
    with (
        patch.object(organizations, "get_tool_db", _tool_db(db)),
        patch.object(organizations, "uuid4", return_value=uuid4()),
    ):
        created = await organizations.create_organization(
            _context(),
            "Midtown Technology Group",
        )

    assert db.committed is True
    assert db.added[0].name == "Midtown Technology Group"
    assert db.added[0].domain == "midtown-technology-group"
    assert db.added[0].created_by == "admin@example.test"
    assert created.structured_content["success"] is True
    assert created.structured_content["domain"] == "midtown-technology-group"


@pytest.mark.asyncio
async def test_create_organization_reports_database_errors():
    with patch.object(organizations, "get_tool_db", _tool_db(_RaisingDb())):
        result = await organizations.create_organization(_context(), "Midtown")

    assert "Error creating organization" in result.structured_content["error"]
    assert "database unavailable" in result.structured_content["error"]


def test_ref_error_payload_shapes_known_ref_errors():
    from bifrost.refs import AmbiguousRefError, RefNotFoundError

    ambiguous = organizations._ref_error_payload(
        AmbiguousRefError("org", "midtown", [{"id": "1"}])
    )
    missing = organizations._ref_error_payload(RefNotFoundError("org", "missing"))
    generic = organizations._ref_error_payload(RuntimeError("boom"))

    assert ambiguous == {
        "kind": "org",
        "value": "midtown",
        "candidates": [{"id": "1"}],
    }
    assert missing == {"kind": "org", "value": "missing"}
    assert generic == {"detail": "boom"}


@pytest.mark.asyncio
async def test_update_and_delete_require_admin_and_refs():
    non_admin = _context(admin=False)

    denied_update = await organizations.update_organization(non_admin, "midtown")
    denied_delete = await organizations.delete_organization(non_admin, "midtown")
    missing_update = await organizations.update_organization(_context(), "")
    missing_delete = await organizations.delete_organization(_context(), "")

    assert "Platform administrator privileges" in denied_update.structured_content["error"]
    assert "Platform administrator privileges" in denied_delete.structured_content["error"]
    assert missing_update.structured_content["error"] == "organization_ref is required"
    assert missing_delete.structured_content["error"] == "organization_ref is required"
