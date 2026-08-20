from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.routers import maintenance


@pytest.mark.asyncio
async def test_index_documentation_returns_skipped_response(monkeypatch) -> None:
    async def index_platform_docs():
        return {"status": "skipped", "reason": "embeddings disabled"}

    monkeypatch.setattr(
        "src.services.docs_indexer.index_platform_docs",
        index_platform_docs,
    )

    response = await maintenance.index_documentation(ctx=None, user=None, db=None)

    assert response.status == "skipped"
    assert response.files_indexed == 0
    assert response.files_unchanged == 0
    assert response.files_deleted == 0
    assert response.message == "embeddings disabled"


@pytest.mark.asyncio
async def test_index_documentation_returns_complete_summary(monkeypatch) -> None:
    async def index_platform_docs():
        return {
            "status": "complete",
            "indexed": 3,
            "skipped": 2,
            "deleted": 1,
        }

    monkeypatch.setattr(
        "src.services.docs_indexer.index_platform_docs",
        index_platform_docs,
    )

    response = await maintenance.index_documentation(ctx=None, user=None, db=None)

    assert response.status == "complete"
    assert response.files_indexed == 3
    assert response.files_unchanged == 2
    assert response.files_deleted == 1
    assert response.message == "Indexed 3 files, 2 unchanged, 1 orphaned removed"


@pytest.mark.asyncio
async def test_index_documentation_returns_failed_for_unexpected_result(monkeypatch) -> None:
    async def index_platform_docs():
        return {"status": "weird", "detail": "unexpected"}

    monkeypatch.setattr(
        "src.services.docs_indexer.index_platform_docs",
        index_platform_docs,
    )

    response = await maintenance.index_documentation(ctx=None, user=None, db=None)

    assert response.status == "failed"
    assert "Unexpected result" in response.message


@pytest.mark.asyncio
async def test_index_documentation_maps_exception_to_http_500(monkeypatch) -> None:
    async def index_platform_docs():
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "src.services.docs_indexer.index_platform_docs",
        index_platform_docs,
    )

    with pytest.raises(HTTPException) as exc_info:
        await maintenance.index_documentation(ctx=None, user=None, db=None)

    assert exc_info.value.status_code == 500
    assert "Failed to index documentation: boom" == exc_info.value.detail


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


@pytest.mark.asyncio
async def test_get_maintenance_status_counts_python_files() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_ScalarResult(7))

    response = await maintenance.get_maintenance_status(ctx=None, user=None, db=db)

    assert response.total_files == 7
    assert response.last_reindex is None


@pytest.mark.asyncio
async def test_get_maintenance_status_maps_db_error_to_http_500() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("db down"))

    with pytest.raises(HTTPException) as exc_info:
        await maintenance.get_maintenance_status(ctx=None, user=None, db=db)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to get maintenance status"


@pytest.mark.asyncio
async def test_cleanup_orphaned_deactivates_workflows_missing_from_file_index() -> None:
    active_id = uuid4()
    missing_id = uuid4()
    active = SimpleNamespace(
        id=active_id,
        name="active",
        display_name="Active",
        path="workflows/active.py",
        is_active=True,
        is_orphaned=False,
    )
    missing = SimpleNamespace(
        id=missing_id,
        name="missing",
        display_name=None,
        path="workflows/missing.py",
        is_active=True,
        is_orphaned=False,
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _RowsResult([("workflows/active.py",)]),
            _RowsResult([active, missing]),
        ]
    )
    db.commit = AsyncMock()

    response = await maintenance.cleanup_orphaned(user=None, db=db)

    assert response.success is True
    assert response.count == 1
    assert response.cleaned[0].entity_id == str(missing_id)
    assert response.cleaned[0].entity_name == "missing"
    assert active.is_active is True
    assert missing.is_active is False
    assert missing.is_orphaned is True
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_orphaned_maps_errors_to_http_500() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("select failed"))

    with pytest.raises(HTTPException) as exc_info:
        await maintenance.cleanup_orphaned(user=None, db=db)

    assert exc_info.value.status_code == 500
    assert "Failed to clean up orphaned entities" in exc_info.value.detail


@pytest.mark.asyncio
async def test_scan_app_dependencies_reports_missing_refs_and_creates_notification(monkeypatch) -> None:
    app_id = uuid4()
    app = SimpleNamespace(
        id=app_id,
        name="Portal",
        slug="portal",
        repo_prefix="apps/portal/",
    )
    workflow_id = uuid4()
    notification_service = SimpleNamespace(
        find_admin_notification_by_title=AsyncMock(return_value=None),
        create_notification=AsyncMock(),
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _RowsResult([app]),
            _RowsResult([(workflow_id, "known_workflow", "workflows/known.py", "run")]),
            _RowsResult(
                [
                    ("apps/portal/page.tsx", "useWorkflow('missing_workflow')"),
                    ("apps/portal/empty.tsx", None),
                ]
            ),
        ]
    )
    monkeypatch.setattr(
        maintenance,
        "parse_dependencies",
        lambda content: {"missing_workflow"} if "missing" in content else set(),
    )
    monkeypatch.setattr(
        maintenance,
        "get_notification_service",
        lambda: notification_service,
    )

    response = await maintenance.scan_app_dependencies(ctx=None, user=None, db=db)

    assert response.apps_scanned == 1
    assert response.files_scanned == 2
    assert response.dependencies_rebuilt == 1
    assert response.issues_found == 1
    assert response.issues[0].app_slug == "portal"
    assert response.issues[0].file_path == "page.tsx"
    assert response.notification_created is True
    notification_service.find_admin_notification_by_title.assert_awaited_once()
    notification_service.create_notification.assert_awaited_once()
    request = notification_service.create_notification.await_args.kwargs["request"]
    assert request.title == "Missing App Dependencies: 1 app(s)"
    assert request.metadata["issues"][0]["dependency_id"] == "missing_workflow"


@pytest.mark.asyncio
async def test_scan_app_dependencies_skips_duplicate_notification(monkeypatch) -> None:
    app = SimpleNamespace(
        id=uuid4(),
        name="Portal",
        slug="portal",
        repo_prefix="apps/portal/",
    )
    notification_service = SimpleNamespace(
        find_admin_notification_by_title=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        create_notification=AsyncMock(),
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _RowsResult([app]),
            _RowsResult([]),
            _RowsResult([("apps/portal/page.tsx", "useWorkflow('missing')")]),
        ]
    )
    monkeypatch.setattr(maintenance, "parse_dependencies", lambda _content: {"missing"})
    monkeypatch.setattr(maintenance, "get_notification_service", lambda: notification_service)

    response = await maintenance.scan_app_dependencies(ctx=None, user=None, db=db)

    assert response.issues_found == 1
    assert response.notification_created is False
    notification_service.create_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_app_dependencies_maps_errors_to_http_500() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("db failed"))

    with pytest.raises(HTTPException) as exc_info:
        await maintenance.scan_app_dependencies(ctx=None, user=None, db=db)

    assert exc_info.value.status_code == 500
    assert "Failed to rebuild app dependencies: db failed" == exc_info.value.detail


@pytest.mark.asyncio
async def test_run_preflight_returns_system_issue_when_file_listing_fails(monkeypatch) -> None:
    service = SimpleNamespace(list_files=AsyncMock(side_effect=RuntimeError("storage down")))
    monkeypatch.setattr(
        "src.services.file_storage.FileStorageService",
        lambda _db: service,
    )

    response = await maintenance.run_preflight(user=None, db=AsyncMock())

    assert response.valid is False
    assert response.issues[0].category == "system"
    assert response.issues[0].detail == "Failed to list files: storage down"


@pytest.mark.asyncio
async def test_run_preflight_warns_for_unregistered_decorated_functions(monkeypatch) -> None:
    files = [
        SimpleNamespace(path="workflows/registered.py"),
        SimpleNamespace(path="workflows/missing.py"),
        SimpleNamespace(path="notes/readme.txt"),
        SimpleNamespace(path="workflows/bad.py"),
    ]
    service = SimpleNamespace(
        list_files=AsyncMock(return_value=files),
        read_file=AsyncMock(
            side_effect=[
                (b"@workflow\ndef registered():\n    pass\n", None),
                (b"@tool()\nasync def missing_tool():\n    pass\n", None),
                (b"def broken(:\n", None),
            ]
        ),
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _RowsResult([SimpleNamespace(id=uuid4())]),
            _RowsResult([]),
        ]
    )
    monkeypatch.setattr(
        "src.services.file_storage.FileStorageService",
        lambda _db: service,
    )

    response = await maintenance.run_preflight(user=None, db=db)

    assert response.valid is True
    assert response.issues == []
    assert len(response.warnings) == 1
    assert response.warnings[0].category == "unregistered_function"
    assert "missing_tool" in response.warnings[0].detail
    assert response.warnings[0].path == "workflows/missing.py"


@pytest.mark.asyncio
async def test_run_preflight_uses_release_only_immutable_source(monkeypatch) -> None:
    service = SimpleNamespace(list_files=AsyncMock(return_value=[]))
    view = SimpleNamespace(
        list=AsyncMock(return_value=["workflows/live_only.py"]),
        governs=lambda path: path == "workflows/live_only.py",
        read=AsyncMock(
            return_value=b"@workflow\nasync def immutable_workflow():\n    pass\n"
        ),
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_RowsResult([]))
    monkeypatch.setattr(
        "src.services.file_storage.FileStorageService",
        lambda _db: service,
    )
    monkeypatch.setattr(
        maintenance,
        "active_workspace_release_file_view",
        AsyncMock(return_value=view),
    )

    response = await maintenance.run_preflight(user=None, db=db)

    assert response.valid is True
    assert response.warnings[0].path == "workflows/live_only.py"
    assert "immutable_workflow" in response.warnings[0].detail
    view.read.assert_awaited_once_with("workflows/live_only.py")

@pytest.fixture(autouse=True)
def bypass_live_registration_authority(monkeypatch):
    """Maintenance examples run without a Workspace Live fixture."""
    monkeypatch.setattr(
        maintenance,
        "guard_workspace_registration_mutation",
        AsyncMock(),
    )
    monkeypatch.setattr(
        maintenance,
        "active_workspace_release_file_view",
        AsyncMock(return_value=None),
    )
