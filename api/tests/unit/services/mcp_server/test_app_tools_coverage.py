from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools import apps
from src.services import workspace_release_files
from src.services.workspace_release_files import WorkspaceReleasePathGoverned


@pytest.fixture(autouse=True)
def _legacy_workspace_authority(monkeypatch: pytest.MonkeyPatch):
    """Keep legacy cases focused when no immutable Live release is active."""
    monkeypatch.setattr(
        workspace_release_files,
        "reject_release_governed_paths",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        workspace_release_files,
        "reject_release_governed_prefixes",
        AsyncMock(return_value=None),
    )


def _context(*, admin: bool = False, org_id=None) -> MCPContext:
    return MCPContext(
        user_id=str(uuid4()),
        org_id=str(org_id if org_id is not None else uuid4()),
        is_platform_admin=admin,
        user_email="admin@example.com" if admin else "user@example.com",
        user_name="Admin User" if admin else "Test User",
    )


def _context_without_org() -> MCPContext:
    return MCPContext(
        user_id=str(uuid4()),
        org_id=None,
        is_platform_admin=False,
        user_email="user@example.com",
        user_name="Test User",
    )


def _app(**overrides):
    row = SimpleNamespace(
        id=uuid4(),
        name="Portal",
        slug="portal",
        description="Customer portal",
        is_published=False,
        has_unpublished_changes=True,
        dependencies=None,
        organization_id=uuid4(),
        solution_id=None,
        repo_path="apps/portal",
        published_snapshot=None,
        published_at=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _ScalarRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FirstResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    def __init__(self, results):
        self._results = list(results)
        self.committed = False
        self.added = []
        self.flushed = False

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("unexpected execute call")
        return self._results.pop(0)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushed = True

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
async def test_list_apps_returns_file_counts_and_handles_errors():
    app = _app(name="Alpha", slug="alpha", is_published=True)
    db = _Db([_ScalarRowsResult([app])])
    storage = MagicMock()
    storage.list_files = AsyncMock(return_value=["_layout.tsx", "pages/index.tsx"])

    with (
        patch.object(apps, "get_tool_db", _tool_db(db)),
        patch("src.services.app_storage.AppStorageService", return_value=storage),
    ):
        result = await apps.list_apps(_context(org_id=app.organization_id))

    assert result.structured_content["count"] == 1
    assert result.structured_content["apps"][0]["status"] == "published"
    assert result.structured_content["apps"][0]["file_count"] == 2

    with patch.object(apps, "get_tool_db", _tool_db(_RaisingDb())):
        failed = await apps.list_apps(_context())

    assert "Error listing apps" in failed.structured_content["error"]
    assert "database unavailable" in failed.structured_content["error"]


@pytest.mark.asyncio
async def test_get_app_by_slug_disambiguates_and_lists_preview_files():
    org_id = uuid4()
    selected = _app(name="Org Portal", slug="shared", organization_id=org_id)
    db = _Db([
        _ScalarRowsResult([
            _app(name="Other", slug="shared", organization_id=uuid4()),
            _app(name="Global", slug="shared", organization_id=None),
            selected,
        ])
    ])
    storage = MagicMock()
    storage.list_files = AsyncMock(return_value=["pages/index.tsx", "_layout.tsx"])

    with (
        patch.object(apps, "get_tool_db", _tool_db(db)),
        patch("src.services.app_storage.AppStorageService", return_value=storage),
    ):
        result = await apps.get_app(_context(org_id=org_id), app_slug="shared")

    assert result.structured_content["name"] == "Org Portal"
    assert result.structured_content["files"] == [
        {"path": "_layout.tsx"},
        {"path": "pages/index.tsx"},
    ]
    storage.list_files.assert_awaited_once_with(str(selected.id), "preview")


@pytest.mark.asyncio
async def test_get_app_reports_missing_rows():
    db = _Db([_ScalarResult(None)])

    with patch.object(apps, "get_tool_db", _tool_db(db)):
        result = await apps.get_app(_context(), app_id=str(uuid4()))

    assert "Application not found" in result.structured_content["error"]


@pytest.mark.asyncio
async def test_get_app_validates_selector_and_wraps_storage_errors():
    missing_selector = await apps.get_app(_context())
    bad_id = await apps.get_app(_context(), app_id="not-a-uuid")

    app = _app()
    db = _Db([_ScalarRowsResult([app])])
    storage = MagicMock()
    storage.list_files = AsyncMock(side_effect=RuntimeError("preview store offline"))

    with (
        patch.object(apps, "get_tool_db", _tool_db(db)),
        patch("src.services.app_storage.AppStorageService", return_value=storage),
    ):
        storage_error = await apps.get_app(_context(org_id=app.organization_id), app_slug=app.slug)

    assert missing_selector.structured_content["error"] == "Either app_id or app_slug is required"
    assert "Invalid app_id format" in bad_id.structured_content["error"]
    assert "Error getting app" in storage_error.structured_content["error"]
    assert "preview store offline" in storage_error.structured_content["error"]


@pytest.mark.asyncio
async def test_update_app_updates_metadata_and_publishes_draft_notice():
    app = _app(name="Old", description="Before")
    db = _Db([_ScalarResult(app)])

    with (
        patch.object(apps, "get_tool_db", _tool_db(db)),
        patch("src.services.solutions.guard.is_solution_managed", return_value=False),
        patch.object(apps, "publish_app_draft_update", new=AsyncMock()) as publish,
    ):
        result = await apps.update_app(
            _context(),
            str(app.id),
            name="New",
            description="After",
        )

    assert db.committed is True
    assert app.name == "New"
    assert app.description == "After"
    assert result.structured_content["updates"] == ["name", "description"]
    publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_app_reports_missing_noop_and_solution_managed():
    missing_db = _Db([_ScalarResult(None)])
    with patch.object(apps, "get_tool_db", _tool_db(missing_db)):
        missing = await apps.update_app(_context(), str(uuid4()), name="New")

    noop_app = _app()
    noop_db = _Db([_ScalarResult(noop_app)])
    with (
        patch.object(apps, "get_tool_db", _tool_db(noop_db)),
        patch("src.services.solutions.guard.is_solution_managed", return_value=False),
    ):
        noop = await apps.update_app(_context(), str(noop_app.id))

    managed_app = _app(solution_id=uuid4())
    managed_db = _Db([_ScalarResult(managed_app)])
    with (
        patch.object(apps, "get_tool_db", _tool_db(managed_db)),
        patch("src.services.solutions.guard.is_solution_managed", return_value=True),
    ):
        managed = await apps.update_app(_context(), str(managed_app.id), name="New")

    assert "Application not found" in missing.structured_content["error"]
    assert noop.structured_content["error"] == "No updates specified"
    assert "Solution-managed" in managed.structured_content["error"]


@pytest.mark.asyncio
async def test_update_app_validates_id_and_shapes_repository_errors():
    bad_id = await apps.update_app(_context(), "not-a-uuid", name="New")

    app = _app()
    db = _Db([_ScalarResult(app)])
    with (
        patch.object(apps, "get_tool_db", _tool_db(db)),
        patch("src.services.solutions.guard.is_solution_managed", return_value=False),
        patch.object(db, "commit", new=AsyncMock(side_effect=RuntimeError("commit failed"))),
    ):
        failed = await apps.update_app(_context(), str(app.id), name="New")

    assert "Invalid app_id format" in bad_id.structured_content["error"]
    assert "Error updating app" in failed.structured_content["error"]
    assert "commit failed" in failed.structured_content["error"]


@pytest.mark.asyncio
async def test_publish_app_queues_canonical_rest_job():
    app_id = str(uuid4())
    body = {"job_id": str(uuid4()), "status": "queued"}

    with patch.object(apps, "call_rest", new=AsyncMock(return_value=(202, body))) as call:
        result = await apps.publish_app(_context(admin=True), app_id)

    call.assert_awaited_once_with(
        call.call_args.args[0],
        "POST",
        f"/api/applications/{app_id}/publish",
        json_body={},
    )
    assert result.structured_content == body
    assert body["job_id"] in result.content[0].text


@pytest.mark.asyncio
async def test_publish_app_reports_nonaccepted_or_malformed_rest_response():
    with patch.object(
        apps,
        "call_rest",
        new=AsyncMock(return_value=(409, {"detail": "locked"})),
    ):
        rejected = await apps.publish_app(_context(), "app-1")

    with patch.object(
        apps,
        "call_rest",
        new=AsyncMock(return_value=(202, "not-json")),
    ):
        malformed = await apps.publish_app(_context(), "app-1")

    assert rejected.structured_content["error"] == "publish_app failed: HTTP 409"
    assert rejected.structured_content["body"] == {"detail": "locked"}
    assert malformed.structured_content["error"] == "publish_app failed: HTTP 202"


@pytest.mark.asyncio
async def test_get_app_dependencies_by_slug_reports_declared_packages():
    app = _app(dependencies={"dayjs": "^1.11.0", "@scope/pkg": "2.0"})
    db = _Db([_ScalarRowsResult([app])])

    with patch.object(apps, "get_tool_db", _tool_db(db)):
        result = await apps.get_app_dependencies(_context(org_id=app.organization_id), app_slug=app.slug)

    assert result.structured_content["dependencies"] == app.dependencies
    assert "dayjs@^1.11.0" in result.content[0].text


@pytest.mark.asyncio
async def test_get_app_dependencies_reports_empty_and_missing_apps():
    empty_app = _app(dependencies=None)
    empty_db = _Db([_ScalarResult(empty_app)])
    with patch.object(apps, "get_tool_db", _tool_db(empty_db)):
        empty = await apps.get_app_dependencies(_context(), app_id=str(empty_app.id))

    missing_db = _Db([_ScalarRowsResult([])])
    with patch.object(apps, "get_tool_db", _tool_db(missing_db)):
        missing = await apps.get_app_dependencies(_context(), app_slug="missing")

    assert empty.structured_content["dependencies"] == {}
    assert "No dependencies declared" in empty.content[0].text
    assert "Application not found" in missing.structured_content["error"]


@pytest.mark.asyncio
async def test_update_app_dependencies_validates_and_invalidates_cache():
    app = _app(dependencies=None)
    db = _Db([_ScalarResult(app)])
    storage = MagicMock()
    storage.invalidate_render_cache = AsyncMock()

    with (
        patch.object(apps, "get_tool_db", _tool_db(db)),
        patch("src.services.solutions.guard.is_solution_managed", return_value=False),
        patch("src.services.app_storage.AppStorageService", return_value=storage),
    ):
        result = await apps.update_app_dependencies(
            _context(org_id=app.organization_id),
            str(app.id),
            {"recharts": "~2.12.7"},
        )

    assert db.committed is True
    assert app.dependencies == {"recharts": "~2.12.7"}
    assert result.structured_content["dependencies"] == {"recharts": "~2.12.7"}
    storage.invalidate_render_cache.assert_awaited_once_with(str(app.id))


@pytest.mark.asyncio
async def test_update_app_dependencies_rejects_invalid_inputs_and_managed_apps():
    app_id = str(uuid4())
    too_many = {f"pkg{i}": "1.0.0" for i in range(21)}

    bad_id = await apps.update_app_dependencies(_context(), "not-a-uuid", {})
    too_many_result = await apps.update_app_dependencies(_context(), app_id, too_many)
    bad_name = await apps.update_app_dependencies(_context(), app_id, {"Bad Name": "1.0.0"})
    bad_version = await apps.update_app_dependencies(_context(), app_id, {"pkg": "latest"})

    managed_app = _app(solution_id=uuid4())
    managed_db = _Db([_ScalarResult(managed_app)])
    with (
        patch.object(apps, "get_tool_db", _tool_db(managed_db)),
        patch("src.services.solutions.guard.is_solution_managed", return_value=True),
    ):
        managed = await apps.update_app_dependencies(_context(), str(managed_app.id), {})

    assert "Invalid app_id format" in bad_id.structured_content["error"]
    assert "Too many dependencies" in too_many_result.structured_content["error"]
    assert "Invalid package name" in bad_name.structured_content["error"]
    assert "Invalid version" in bad_version.structured_content["error"]
    assert "Solution-managed" in managed.structured_content["error"]


@pytest.mark.asyncio
async def test_create_app_validates_scope_org_and_stale_source():
    no_name = await apps.create_app(_context(), "")
    bad_scope = await apps.create_app(_context(), "Portal", scope="tenant")
    bad_org = await apps.create_app(
        _context_without_org(),
        "Portal",
        organization_id="not-a-uuid",
    )
    missing_org = await apps.create_app(
        _context_without_org(),
        "Portal",
        scope="organization",
    )
    db = _Db([_ScalarRowsResult([])])
    with (
        patch.object(apps, "get_tool_db", _tool_db(db)),
        patch(
            "src.routers.applications.ensure_no_stale_app_source",
            new=AsyncMock(side_effect=ValueError("Source files already exist")),
        ),
    ):
        stale = await apps.create_app(_context(), "Customer Portal")

    assert no_name.structured_content["error"] == "name is required"
    assert bad_scope.structured_content["error"] == "scope must be 'global' or 'organization'"
    assert "not a valid UUID" in bad_org.structured_content["error"]
    assert "organization_id is required" in missing_org.structured_content["error"]
    assert stale.structured_content["error"] == "Source files already exist"


@pytest.mark.asyncio
async def test_create_app_scaffolds_global_app_and_detects_duplicates():
    duplicate_db = _Db([_FirstResult(("existing-id",))])
    with patch.object(apps, "get_tool_db", _tool_db(duplicate_db)):
        duplicate = await apps.create_app(
            _context(admin=True),
            "Existing Portal",
            slug="existing",
            scope="global",
        )

    db = _Db([_RowsResult([])])
    file_storage = MagicMock()
    file_storage.write_file = AsyncMock()

    with (
        patch.object(apps, "get_tool_db", _tool_db(db)),
        patch("src.routers.applications.ensure_no_stale_app_source", new=AsyncMock()),
        patch("src.services.file_storage.FileStorageService", return_value=file_storage),
    ):
        created = await apps.create_app(
            _context(admin=True),
            "Customer Portal!",
            description="Dashboards",
            scope="global",
        )

    assert duplicate.structured_content["error"] == "Application with slug 'existing' already exists"
    assert db.flushed is True
    assert db.committed is True
    assert db.added[0].organization_id is None
    assert db.added[0].slug == "customer-portal"
    assert created.structured_content["file_count"] == 2
    assert file_storage.write_file.await_count == 2


@pytest.mark.asyncio
async def test_create_app_shapes_scaffold_storage_errors():
    db = _Db([_RowsResult([])])
    file_storage = MagicMock()
    file_storage.write_file = AsyncMock(side_effect=RuntimeError("repo write failed"))

    with (
        patch.object(apps, "get_tool_db", _tool_db(db)),
        patch("src.routers.applications.ensure_no_stale_app_source", new=AsyncMock()),
        patch("src.services.file_storage.FileStorageService", return_value=file_storage),
    ):
        failed = await apps.create_app(_context(), "Customer Portal")

    assert db.flushed is True
    assert db.committed is False
    assert "Error creating app" in failed.structured_content["error"]
    assert "repo write failed" in failed.structured_content["error"]


@pytest.mark.asyncio
async def test_publish_app_forwards_identifier_validation_to_rest_router():
    with patch.object(
        apps,
        "call_rest",
        new=AsyncMock(return_value=(422, {"detail": "invalid app id"})),
    ) as call:
        result = await apps.publish_app(_context(), "not-a-uuid")

    call.assert_awaited_once()
    assert call.call_args.args[2] == "/api/applications/not-a-uuid/publish"
    assert result.structured_content["error"] == "publish_app failed: HTTP 422"
    assert result.structured_content["body"] == {"detail": "invalid app id"}


@pytest.mark.asyncio
async def test_replace_app_serializes_rest_success_and_errors():
    missing_id = await apps.replace_app(_context(), "", "apps/new")
    missing_path = await apps.replace_app(_context(), str(uuid4()), "")

    with patch.object(apps, "call_rest", new=AsyncMock(return_value=(409, {"detail": "nested"}))) as call:
        failed = await apps.replace_app(_context(), "app-1", "apps/new", force=True)

    with patch.object(apps, "call_rest", new=AsyncMock(return_value=(200, "ok"))):
        ok_text = await apps.replace_app(_context(), "app-1", "apps/new")

    with patch.object(apps, "call_rest", new=AsyncMock(return_value=(200, {"repo_path": "apps/new"}))):
        ok_dict = await apps.replace_app(_context(), "app-1", "apps/new")

    assert missing_id.structured_content["error"] == "app_id is required"
    assert missing_path.structured_content["error"] == "repo_path is required"
    assert "replace_app failed: HTTP 409" in failed.structured_content["error"]
    assert failed.structured_content["body"] == {"detail": "nested"}
    call.assert_awaited_once_with(
        call.call_args.args[0],
        "POST",
        "/api/applications/app-1/replace",
        json_body={"repo_path": "apps/new", "force": True},
    )
    assert ok_text.structured_content == {"body": "ok"}
    assert ok_dict.structured_content["repo_path"] == "apps/new"


@pytest.mark.asyncio
async def test_push_files_rejects_solution_managed_writes_and_delete_sweeps():
    managed = _app(repo_path="apps/managed", solution_id=uuid4())
    unmanaged = _app(repo_path="apps/open")

    write_db = _Db([_ScalarRowsResult([managed, unmanaged])])
    with (
        patch.object(apps, "get_tool_db", _tool_db(write_db)),
        patch("src.services.solutions.guard.is_solution_managed", side_effect=lambda app: app.solution_id is not None),
    ):
        blocked_write = await apps.push_files(
            _context(),
            {
                "apps/open/pages/index.tsx": "export default function Open() {}",
                "apps/managed/pages/index.tsx": "export default function Managed() {}",
            },
        )

    delete_db = _Db([_ScalarRowsResult([managed, unmanaged])])
    with (
        patch.object(apps, "get_tool_db", _tool_db(delete_db)),
        patch("src.services.solutions.guard.is_solution_managed", side_effect=lambda app: app.solution_id is not None),
    ):
        blocked_delete = await apps.push_files(
            _context(),
            {"apps/open/pages/index.tsx": "export default function Open() {}"},
            delete_missing_prefix="apps",
        )

    assert "Solution-managed" in blocked_write.structured_content["error"]
    assert blocked_write.structured_content["blocked_paths"] == ["apps/managed/pages/index.tsx"]
    assert "Solution-managed" in blocked_delete.structured_content["error"]
    assert blocked_delete.structured_content["blocked_delete_prefix"] == "apps"


@pytest.mark.asyncio
async def test_push_files_rejects_governed_python_before_any_write(monkeypatch):
    app = _app(repo_path="apps/portal")
    db = _Db([_ScalarRowsResult([app])])
    guard = AsyncMock(
        side_effect=WorkspaceReleasePathGoverned(
            "apps/portal/workflow.py", "release-1"
        )
    )
    file_storage = MagicMock()
    file_storage.write_file = AsyncMock()
    monkeypatch.setattr(workspace_release_files, "reject_release_governed_paths", guard)

    with (
        patch.object(apps, "get_tool_db", _tool_db(db)),
        patch("src.services.solutions.guard.is_solution_managed", return_value=False),
        patch("src.services.file_storage.FileStorageService", return_value=file_storage),
    ):
        result = await apps.push_files(
            _context(),
            {
                "apps/portal/pages/index.tsx": "export default function Index() {}",
                "apps/portal/workflow.py": "reviewed = True",
            },
        )

    assert "governed by active workspace-release-v1" in result.structured_content["error"]
    assert guard.await_args.args[2] == ["apps/portal/workflow.py"]
    file_storage.write_file.assert_not_awaited()
    assert db.committed is False


@pytest.mark.asyncio
async def test_push_files_rejects_governed_delete_prefix_before_any_write(monkeypatch):
    app = _app(repo_path="apps/portal")
    db = _Db([_ScalarRowsResult([app])])
    guard = AsyncMock(
        side_effect=WorkspaceReleasePathGoverned(
            "apps/portal/workflow.py", "release-1"
        )
    )
    file_storage = MagicMock()
    file_storage.write_file = AsyncMock()
    file_storage.delete_file = AsyncMock()
    monkeypatch.setattr(workspace_release_files, "reject_release_governed_prefixes", guard)

    with (
        patch.object(apps, "get_tool_db", _tool_db(db)),
        patch("src.services.solutions.guard.is_solution_managed", return_value=False),
        patch("src.services.file_storage.FileStorageService", return_value=file_storage),
    ):
        result = await apps.push_files(
            _context(),
            {"apps/portal/pages/index.tsx": "export default function Index() {}"},
            delete_missing_prefix="apps/portal",
        )

    assert "governed by active workspace-release-v1" in result.structured_content["error"]
    assert guard.await_args.args[2] == ["apps/portal"]
    file_storage.write_file.assert_not_awaited()
    file_storage.delete_file.assert_not_awaited()
    assert db.committed is False


@pytest.mark.asyncio
async def test_push_files_counts_unchanged_created_deleted_and_compile_warnings():
    app = _app(repo_path="apps/portal")
    unchanged_hash = "hash-present"
    db = _Db([
        _ScalarRowsResult([app]),
        _ScalarResult(unchanged_hash),
        _ScalarResult(None),
        _RowsResult([
            ("apps/portal/pages/old.tsx",),
            ("apps/portal/pages/index.tsx",),
        ]),
    ])
    file_storage = MagicMock()
    file_storage.write_file = AsyncMock()
    file_storage.delete_file = AsyncMock()
    app_storage = MagicMock()
    app_storage.write_preview_file = AsyncMock()

    compile_result = SimpleNamespace(
        success=False,
        compiled=None,
        path="pages/new.tsx",
        error="syntax error",
    )
    compiler = MagicMock()
    compiler.compile_batch = AsyncMock(return_value=[compile_result])

    with (
        patch.object(apps, "get_tool_db", _tool_db(db)),
        patch("src.services.solutions.guard.is_solution_managed", return_value=False),
        patch("hashlib.sha256", return_value=SimpleNamespace(hexdigest=lambda: unchanged_hash)),
        patch("src.services.file_storage.FileStorageService", return_value=file_storage),
        patch("src.services.app_compiler.AppCompilerService", return_value=compiler),
        patch("src.services.app_storage.AppStorageService", return_value=app_storage),
    ):
        result = await apps.push_files(
            _context(),
            {
                "apps/portal/README.md": "same",
                "apps/portal/pages/new.tsx": "export default function New() {}",
            },
            delete_missing_prefix="apps/portal",
        )

    assert db.committed is True
    assert result.structured_content["unchanged"] == 1
    assert result.structured_content["created"] == 1
    assert result.structured_content["deleted"] == 2
    assert result.structured_content["compile_warnings"] == ["✗ pages/new.tsx: syntax error"]
    file_storage.write_file.assert_awaited_once()
    assert file_storage.delete_file.await_count == 2


@pytest.mark.asyncio
async def test_push_files_counts_updates_and_surfaces_write_delete_errors():
    app = _app(repo_path="apps/portal")
    db = _Db([
        _ScalarRowsResult([app]),
        _ScalarResult("old-hash"),
        _ScalarResult(None),
        _RowsResult([
            ("apps/portal/pages/delete-me.tsx",),
            ("apps/portal/pages/write-fail.tsx",),
        ]),
    ])
    file_storage = MagicMock()
    file_storage.write_file = AsyncMock(side_effect=[None, RuntimeError("write denied")])
    file_storage.delete_file = AsyncMock(side_effect=RuntimeError("delete denied"))
    compiler = MagicMock()
    compiler.compile_batch = AsyncMock(return_value=[
        SimpleNamespace(success=True, compiled="compiled", path="pages/index.tsx", error=None)
    ])
    app_storage = MagicMock()
    app_storage.write_preview_file = AsyncMock()

    with (
        patch.object(apps, "get_tool_db", _tool_db(db)),
        patch("src.services.solutions.guard.is_solution_managed", return_value=False),
        patch("hashlib.sha256", return_value=SimpleNamespace(hexdigest=lambda: "new-hash")),
        patch("src.services.file_storage.FileStorageService", return_value=file_storage),
        patch("src.services.app_compiler.AppCompilerService", return_value=compiler),
        patch("src.services.app_storage.AppStorageService", return_value=app_storage),
    ):
        result = await apps.push_files(
            _context(),
            {
                "apps/portal/pages/index.tsx": "export default function Index() {}",
                "apps/portal/pages/write-fail.tsx": "export default function Bad() {}",
            },
            delete_missing_prefix="apps/portal/pages",
        )

    assert db.committed is True
    assert result.structured_content["updated"] == 1
    assert result.structured_content["created"] == 0
    assert result.structured_content["deleted"] == 0
    assert result.structured_content["errors"] == [
        "apps/portal/pages/write-fail.tsx: write denied",
        "delete apps/portal/pages/delete-me.tsx: delete denied",
    ]
    app_storage.write_preview_file.assert_awaited_once_with(
        str(app.id),
        "pages/index.tsx",
        b"compiled",
    )


@pytest.mark.asyncio
async def test_validate_app_reports_compile_structure_dependency_and_workflow_errors():
    workflow_ref = str(uuid4())
    app = _app(
        name="Validation Target",
        dependencies={"recharts": "2.12.7"},
        repo_prefix="apps/portal/",
    )
    files = [
        SimpleNamespace(path="apps/portal/_layout.tsx", content="export default function Layout(){return <>{children}</>}"),
        SimpleNamespace(
            path="apps/portal/pages/index.tsx",
            content=(
                "import dayjs from 'dayjs';\n"
                f"export default function Index(){{useWorkflowQuery('{workflow_ref}');"
                "useWorkflowMutation('not-a-uuid'); return null;}}"
            ),
        ),
        SimpleNamespace(path="apps/portal/components/Card.tsx", content="export const Card = () => null"),
    ]
    db = _Db([
        _ScalarResult(app),
        _RowsResult(files),
        _ScalarResult(None),
    ])
    compiler = MagicMock()
    compiler.compile_batch = AsyncMock(return_value=[
        SimpleNamespace(success=True, default_export="Layout", error=None),
        SimpleNamespace(success=False, default_export=None, error="tsx syntax error"),
        SimpleNamespace(success=True, default_export=None, error=None),
    ])

    with (
        patch.object(apps, "get_tool_db", _tool_db(db)),
        patch("src.services.app_compiler.AppCompilerService", return_value=compiler),
    ):
        result = await apps.validate_app(_context(), str(app.id))

    messages = [item["message"] for item in result.structured_content["errors"]]
    warning_messages = [item["message"] for item in result.structured_content["warnings"]]
    assert result.structured_content["valid"] is False
    assert "Compilation failed: tsx syntax error" in messages
    assert any("Layout uses {children}" in message for message in messages)
    assert any("Missing default export" in message for message in messages)
    assert f"Workflow '{workflow_ref}' not found" in messages
    assert "'not-a-uuid' is not a valid UUID" in messages
    assert "Missing dependency: 'dayjs' is imported but not declared in app dependencies" in messages
    assert "Unused dependency: 'recharts' is declared but not imported by any file" in warning_messages


@pytest.mark.asyncio
async def test_validate_app_reports_invalid_missing_and_repository_errors():
    bad_id = await apps.validate_app(_context(), "not-a-uuid")

    missing_db = _Db([_ScalarResult(None)])
    with patch.object(apps, "get_tool_db", _tool_db(missing_db)):
        missing = await apps.validate_app(_context(), str(uuid4()))

    app = _app(repo_prefix="apps/portal/")
    failing_db = _Db([_ScalarResult(app)])
    with patch.object(apps, "get_tool_db", _tool_db(failing_db)):
        failed = await apps.validate_app(_context(), str(app.id))

    assert "Invalid app_id format" in bad_id.structured_content["error"]
    assert "Application not found" in missing.structured_content["error"]
    assert "Error validating app" in failed.structured_content["error"]
    assert "unexpected execute call" in failed.structured_content["error"]


@pytest.mark.asyncio
async def test_get_and_update_app_dependencies_validate_missing_and_remove_all():
    missing_selector = await apps.get_app_dependencies(_context())
    bad_id = await apps.get_app_dependencies(_context(), app_id="not-a-uuid")

    missing_db = _Db([_ScalarResult(None)])
    with patch.object(apps, "get_tool_db", _tool_db(missing_db)):
        missing_app = await apps.update_app_dependencies(_context(), str(uuid4()), {})

    app = _app(dependencies={"dayjs": "^1.11.0"})
    db = _Db([_ScalarResult(app)])
    storage = MagicMock()
    storage.invalidate_render_cache = AsyncMock()
    with (
        patch.object(apps, "get_tool_db", _tool_db(db)),
        patch("src.services.solutions.guard.is_solution_managed", return_value=False),
        patch("src.services.app_storage.AppStorageService", return_value=storage),
    ):
        removed = await apps.update_app_dependencies(_context(), str(app.id), {})

    assert missing_selector.structured_content["error"] == "Either app_id or app_slug is required"
    assert "Invalid app_id format" in bad_id.structured_content["error"]
    assert "Application not found" in missing_app.structured_content["error"]
    assert app.dependencies is None
    assert removed.structured_content["dependencies"] == {}
    assert "Removed all dependencies" in removed.content[0].text


@pytest.mark.asyncio
async def test_get_app_schema_and_register_tools_expose_app_tool_metadata():
    schema = await apps.get_app_schema(_context())

    registered = []

    def fake_register_tool_with_context(_mcp, func, tool_id, description, get_context_fn):
        registered.append((func, tool_id, description, get_context_fn))

    get_context = object()
    with patch(
        "src.services.mcp_server.generators.fastmcp_generator.register_tool_with_context",
        side_effect=fake_register_tool_with_context,
    ):
        apps.register_tools(MagicMock(), get_context)

    assert "App Builder Schema Documentation" in schema.structured_content["schema"]
    assert "Application Models" in schema.structured_content["schema"]
    registered_ids = [item[1] for item in registered]
    assert registered_ids == [tool_id for tool_id, _name, _description in apps.TOOLS]
    assert dict((tool_id, func) for func, tool_id, _description, _ctx in registered)["push_files"] is apps.push_files
    assert all(item[3] is get_context for item in registered)
