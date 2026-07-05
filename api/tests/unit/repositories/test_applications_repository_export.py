from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.models.orm.applications import Application
from src.repositories.applications import ApplicationRepository


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _RowsResult(self._rows)


async def test_export_application_serializes_repo_files_and_role_ids(monkeypatch):
    """Export returns portable app metadata plus repo-relative source files."""
    app_id = uuid.uuid4()
    role_ids = [uuid.uuid4(), uuid.uuid4()]
    published_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    created_at = datetime(2026, 1, 1, 3, 4, 5, tzinfo=timezone.utc)
    updated_at = datetime(2026, 1, 3, 3, 4, 5, tzinfo=timezone.utc)
    app = Application(
        id=app_id,
        name="Dashboard",
        slug="dashboard",
        description="Ops dashboard",
        icon="LayoutDashboard",
        repo_path="apps/dashboard",
        organization_id=None,
        created_by="builder@example.com",
        access_level="role_based",
        app_model="inline_v1",
        published_snapshot={"pages/index.tsx": "abc"},
        published_at=published_at,
        created_at=created_at,
        updated_at=updated_at,
    )
    session = _FakeSession(
        [
            SimpleNamespace(path="apps/dashboard/_layout.tsx", content="layout"),
            SimpleNamespace(path="apps/dashboard/pages/index.tsx", content="page"),
            SimpleNamespace(path="apps/dashboard/pages/empty.tsx", content=None),
        ]
    )
    repo = ApplicationRepository(session, org_id=None, user_id=None, is_superuser=True)
    monkeypatch.setattr(repo, "get_role_ids", AsyncMock(return_value=role_ids))

    exported = await repo.export_application(app)

    assert exported["id"] == str(app_id)
    assert exported["name"] == "Dashboard"
    assert exported["slug"] == "dashboard"
    assert exported["description"] == "Ops dashboard"
    assert exported["icon"] == "LayoutDashboard"
    assert exported["organization_id"] is None
    assert exported["published_at"] == published_at.isoformat()
    assert exported["created_at"] == created_at.isoformat()
    assert exported["updated_at"] == updated_at.isoformat()
    assert exported["created_by"] == "builder@example.com"
    assert exported["is_published"] is True
    assert exported["has_unpublished_changes"] is True
    assert exported["access_level"] == "role_based"
    assert exported["role_ids"] == [str(role_id) for role_id in role_ids]
    assert exported["files"] == [
        {"path": "_layout.tsx", "source": "layout"},
        {"path": "pages/index.tsx", "source": "page"},
        {"path": "pages/empty.tsx", "source": ""},
    ]


async def test_update_draft_files_replaces_existing_prefix_with_system_writes(
    monkeypatch,
):
    """Draft replacement deletes stale app files and writes new source paths."""
    app = Application(
        id=uuid.uuid4(),
        name="Dashboard",
        slug="dashboard",
        repo_path="apps/dashboard",
        organization_id=None,
    )
    session = _FakeSession(
        [
            ("apps/dashboard/_layout.tsx",),
            ("apps/dashboard/pages/stale.tsx",),
        ]
    )
    calls: list[tuple[str, str, str | None]] = []

    class FakeFileStorageService:
        def __init__(self, storage_session):
            assert storage_session is session

        async def delete_file(self, path: str):
            calls.append(("delete", path, None))

        async def write_file(self, path: str, content: bytes, updated_by: str):
            calls.append(("write", path, content.decode("utf-8")))
            assert updated_by == "system"

    monkeypatch.setattr(
        "src.services.file_storage.FileStorageService",
        FakeFileStorageService,
    )

    repo = ApplicationRepository(session, org_id=None, user_id=None, is_superuser=True)
    await repo.update_draft_files(
        app,
        [
            {"path": "_layout.tsx", "source": "new layout"},
            {"path": "pages/index.tsx", "source": "new page"},
            {"path": "pages/empty.tsx"},
        ],
    )

    assert calls == [
        ("delete", "apps/dashboard/_layout.tsx", None),
        ("delete", "apps/dashboard/pages/stale.tsx", None),
        ("write", "apps/dashboard/_layout.tsx", "new layout"),
        ("write", "apps/dashboard/pages/index.tsx", "new page"),
        ("write", "apps/dashboard/pages/empty.tsx", ""),
    ]
