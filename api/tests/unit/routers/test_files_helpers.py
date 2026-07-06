from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.policies import FilePolicies
from src.routers import files


ORG_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
ORG_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
USER_ID = UUID("11111111-1111-1111-1111-111111111111")
SOLUTION_ID = UUID("22222222-2222-2222-2222-222222222222")


class _FakeDb:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def _ctx(
    *,
    org_id: UUID | None = ORG_A,
    user_org_id: UUID | None = ORG_A,
    is_superuser: bool = False,
    solution_id: UUID | str | None = None,
):
    return SimpleNamespace(
        org_id=org_id,
        solution_id=solution_id,
        app_id=None,
        db=_FakeDb(),
        user=UserPrincipal(
            user_id=USER_ID,
            email="user@example.test",
            organization_id=user_org_id,
            is_superuser=is_superuser,
        ),
    )


def test_file_org_id_pins_regular_users_and_honors_superuser_scope():
    regular = _ctx(org_id=ORG_A, user_org_id=ORG_A, is_superuser=False)
    assert files._file_org_id(regular, "uploads", str(ORG_B)) == ORG_A
    assert files._file_org_id(regular, "workspace", str(ORG_B)) is None

    admin = _ctx(org_id=ORG_A, user_org_id=ORG_A, is_superuser=True)
    assert files._file_org_id(admin, "reports", str(ORG_B)) == ORG_B
    assert files._file_org_id(admin, "reports", "global") is None
    assert files._file_org_id(admin, "reports", None) == ORG_A


def test_storage_scope_uses_global_segment_for_unscoped_files():
    assert files._storage_scope(ORG_A) == str(ORG_A)
    assert files._storage_scope(None) == "global"


def test_resolve_effective_scope_prefers_solution_context_and_rejects_workspace():
    ctx = _ctx(solution_id=SOLUTION_ID, is_superuser=True)

    assert files._resolve_effective_scope(ctx, "reports", str(ORG_B)) == str(SOLUTION_ID)

    with pytest.raises(HTTPException) as exc_info:
        files._resolve_effective_scope(ctx, "workspace", None)
    assert exc_info.value.status_code == 400
    assert "workspace is not available" in exc_info.value.detail


def test_resolve_effective_scope_falls_back_to_requested_non_uuid_scope_for_scopeless_admin():
    ctx = _ctx(org_id=None, user_org_id=None, is_superuser=True)

    assert files._resolve_effective_scope(ctx, "temp", "org-a") == "org-a"


def test_ctx_solution_id_parses_valid_values_and_ignores_bad_values():
    assert files._ctx_solution_id(_ctx(solution_id=str(SOLUTION_ID)), "reports") == SOLUTION_ID
    assert files._ctx_solution_id(_ctx(solution_id=SOLUTION_ID), "reports") == SOLUTION_ID
    assert files._ctx_solution_id(_ctx(solution_id="not-a-uuid"), "reports") is None
    assert files._ctx_solution_id(_ctx(solution_id=None), "reports") is None


def test_organization_id_for_policy_parses_global_and_uuid_scope():
    assert files._organization_id_for_policy("reports", None) is None
    assert files._organization_id_for_policy("reports", "global") is None
    assert files._organization_id_for_policy("reports", str(ORG_A)) == ORG_A


def test_relative_list_path_strips_resolved_prefix_only_for_matching_location():
    assert files._relative_list_path(
        f"uploads/{ORG_A}/nested/report.pdf",
        location="uploads",
        scope=str(ORG_A),
    ) == "nested/report.pdf"
    assert files._relative_list_path(
        "other/location/report.pdf",
        location="uploads",
        scope=str(ORG_A),
    ) == "other/location/report.pdf"
    assert files._relative_list_path(
        "workspace/file.py",
        location="workspace",
        scope=None,
    ) == "workspace/file.py"


def test_tiers_for_backend_mode_limits_local_to_primary_tier():
    tiers = ["cloud", "local", "fallback"]

    assert files._tiers_for_backend_mode(tiers, "local") == ["cloud"]
    assert files._tiers_for_backend_mode(tiers, "cloud") == tiers


def test_policy_document_accepts_model_or_raw_policy_list():
    existing = FilePolicies(policies=[])
    assert files._policy_document(existing) is existing

    parsed = files._policy_document([
        {"name": "Readers", "actions": ["read"]}
    ])

    assert isinstance(parsed, FilePolicies)
    assert parsed.policies[0].name == "Readers"
    assert parsed.policies[0].actions == ["read"]


def test_policy_public_serializes_row_shape():
    policies = FilePolicies(
        policies=[{"name": "Writers", "actions": ["write"]}]
    )
    row = SimpleNamespace(
        id=UUID("33333333-3333-3333-3333-333333333333"),
        organization_id=ORG_A,
        location="reports",
        path="exports/",
        policies=policies.model_dump(mode="json"),
    )

    public = files._policy_public(row)

    assert public.id == "33333333-3333-3333-3333-333333333333"
    assert public.organization_id == str(ORG_A)
    assert public.location == "reports"
    assert public.path == "exports/"
    assert public.policies.policies[0].name == "Writers"
    assert public.policies.policies[0].actions == ["write"]


@pytest.mark.asyncio
async def test_read_file_uses_first_allowed_existing_tier(monkeypatch):
    authorize_calls = []
    read_calls = []

    tiers = [
        SimpleNamespace(scope="global", solution_id=None, organization_id=None),
        SimpleNamespace(scope=str(ORG_A), solution_id=None, organization_id=ORG_A),
    ]

    class FakeBackend:
        async def read(self, path, location, *, scope):
            read_calls.append((path, location, scope))
            return b"report body"

    async def fake_file_read_tiers(db, ctx, location, scope):
        return tiers

    async def fake_authorize(ctx, **kwargs):
        authorize_calls.append(kwargs)
        return kwargs["scope"] == str(ORG_A)

    monkeypatch.setattr("src.services.solution_scope.file_read_tiers", fake_file_read_tiers)
    monkeypatch.setattr(files, "_authorize_file_policy", fake_authorize)
    monkeypatch.setattr(files, "get_backend", lambda mode, db: FakeBackend())

    response = await files.read_file(
        files.FileReadRequest(path="reports/q1.txt", location="reports"),
        _ctx(),
        SimpleNamespace(user_id=USER_ID),
        db=SimpleNamespace(),
    )

    assert response.content == "report body"
    assert response.binary is False
    assert [call["scope"] for call in authorize_calls] == ["global", str(ORG_A)]
    assert read_calls == [("reports/q1.txt", "reports", str(ORG_A))]


@pytest.mark.asyncio
async def test_read_file_binary_encodes_content(monkeypatch):
    class FakeBackend:
        async def read(self, path, location, *, scope):
            return b"\x00\x01raw"

    async def fake_file_read_tiers(db, ctx, location, scope):
        return [SimpleNamespace(scope=str(ORG_A), solution_id=None, organization_id=ORG_A)]

    monkeypatch.setattr("src.services.solution_scope.file_read_tiers", fake_file_read_tiers)
    monkeypatch.setattr(files, "_authorize_file_policy", lambda *_args, **_kwargs: _async_value(True))
    monkeypatch.setattr(files, "get_backend", lambda mode, db: FakeBackend())

    response = await files.read_file(
        files.FileReadRequest(
            path="reports/raw.bin",
            location="reports",
            binary=True,
            scope=str(ORG_A),
        ),
        _ctx(),
        SimpleNamespace(user_id=USER_ID),
        db=SimpleNamespace(),
    )

    assert response.content == base64.b64encode(b"\x00\x01raw").decode()
    assert response.binary is True


@pytest.mark.asyncio
async def test_read_file_reports_forbidden_when_no_tier_is_authorized(monkeypatch):
    async def fake_file_read_tiers(db, ctx, location, scope):
        return [SimpleNamespace(scope=str(ORG_A), solution_id=None, organization_id=ORG_A)]

    monkeypatch.setattr("src.services.solution_scope.file_read_tiers", fake_file_read_tiers)
    monkeypatch.setattr(files, "_authorize_file_policy", lambda *_args, **_kwargs: _async_value(False))
    monkeypatch.setattr(files, "get_backend", lambda mode, db: SimpleNamespace())

    with pytest.raises(HTTPException) as exc_info:
        await files.read_file(
            files.FileReadRequest(path="reports/secret.txt", location="reports"),
            _ctx(),
            SimpleNamespace(user_id=USER_ID),
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["message"] == "File policy denied"
    assert exc_info.value.detail["action"] == "read"
    assert exc_info.value.detail["location"] == "reports"
    assert exc_info.value.detail["path"] == "reports/secret.txt"
    assert exc_info.value.detail["scope"] is None


@pytest.mark.asyncio
async def test_read_file_reports_not_found_after_authorized_tiers_miss(monkeypatch):
    class FakeBackend:
        async def read(self, path, location, *, scope):
            raise FileNotFoundError(path)

    async def fake_file_read_tiers(db, ctx, location, scope):
        return [SimpleNamespace(scope=str(ORG_A), solution_id=None, organization_id=ORG_A)]

    monkeypatch.setattr("src.services.solution_scope.file_read_tiers", fake_file_read_tiers)
    monkeypatch.setattr(files, "_authorize_file_policy", lambda *_args, **_kwargs: _async_value(True))
    monkeypatch.setattr(files, "get_backend", lambda mode, db: FakeBackend())

    with pytest.raises(HTTPException) as exc_info:
        await files.read_file(
            files.FileReadRequest(path="reports/missing.txt", location="reports"),
            _ctx(),
            SimpleNamespace(user_id=USER_ID),
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "File not found: reports/missing.txt"


@pytest.mark.asyncio
async def test_read_file_rejects_binary_when_text_requested(monkeypatch):
    class FakeBackend:
        async def read(self, path, location, *, scope):
            return b"\xff\xfe"

    async def fake_file_read_tiers(db, ctx, location, scope):
        return [SimpleNamespace(scope=str(ORG_A), solution_id=None, organization_id=ORG_A)]

    monkeypatch.setattr("src.services.solution_scope.file_read_tiers", fake_file_read_tiers)
    monkeypatch.setattr(files, "_authorize_file_policy", lambda *_args, **_kwargs: _async_value(True))
    monkeypatch.setattr(files, "get_backend", lambda mode, db: FakeBackend())

    with pytest.raises(HTTPException) as exc_info:
        await files.read_file(
            files.FileReadRequest(path="reports/raw.bin", location="reports"),
            _ctx(),
            SimpleNamespace(user_id=USER_ID),
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "File is binary. Use binary=true to read as base64."


@pytest.mark.asyncio
async def test_write_file_cloud_records_metadata_and_publishes(monkeypatch):
    writes = []
    metadata_calls = []
    publish_calls = []

    class FakeBackend:
        async def write(self, path, content, location, updated_by, *, scope):
            writes.append((path, content, location, updated_by, scope))

    class FakeStorage:
        def __init__(self, db):
            self.db = db

        async def record_file_write_metadata(self, **kwargs):
            metadata_calls.append(kwargs)

    async def fake_publish_file_change(**kwargs):
        publish_calls.append(kwargs)

    monkeypatch.setattr(files, "_require_declared_solution_file_location", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(files, "_require_file_policy", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(files, "_install_org_id", lambda *_args, **_kwargs: _async_value(ORG_A))
    monkeypatch.setattr(files, "get_backend", lambda mode, db: FakeBackend())
    monkeypatch.setattr(files, "FileStorageService", FakeStorage)
    monkeypatch.setattr("src.core.pubsub.publish_file_change", fake_publish_file_change)

    await files.write_file(
        files.FileWriteRequest(
            path="folder/report.txt",
            content="hello",
            location="reports",
            scope=str(ORG_A),
            mode="cloud",
        ),
        _ctx(),
        SimpleNamespace(user_id=USER_ID),
        db=SimpleNamespace(),
    )

    assert writes == [("folder/report.txt", b"hello", "reports", "user@example.test", str(ORG_A))]
    assert metadata_calls[0]["location"] == "reports"
    assert metadata_calls[0]["scope"] == str(ORG_A)
    assert metadata_calls[0]["path"] == "folder/report.txt"
    assert metadata_calls[0]["s3_path"] == f"reports/{ORG_A}/folder/report.txt"
    assert metadata_calls[0]["size_bytes"] == 5
    assert metadata_calls[0]["updated_by"] == "user@example.test"
    assert metadata_calls[0]["org_id"] == ORG_A
    assert publish_calls == [
        {
            "location": "reports",
            "scope": str(ORG_A),
            "path": "folder/report.txt",
            "action": "write",
        }
    ]


@pytest.mark.asyncio
async def test_write_file_local_binary_decodes_without_cloud_metadata(monkeypatch):
    writes = []

    class FakeBackend:
        async def write(self, path, content, location, updated_by, *, scope):
            writes.append((path, content, location, updated_by, scope))

    monkeypatch.setattr(files, "_require_declared_solution_file_location", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(files, "_require_file_policy", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(files, "get_backend", lambda mode, db: FakeBackend())

    await files.write_file(
        files.FileWriteRequest(
            path="folder/raw.bin",
            content=base64.b64encode(b"\x00\x01raw").decode(),
            binary=True,
            location="reports",
            scope=str(ORG_A),
            mode="local",
        ),
        _ctx(),
        SimpleNamespace(user_id=USER_ID),
        db=SimpleNamespace(),
    )

    assert writes == [("folder/raw.bin", b"\x00\x01raw", "reports", "user@example.test", str(ORG_A))]


@pytest.mark.asyncio
async def test_delete_file_cloud_removes_metadata_flushes_and_publishes(monkeypatch):
    deletes = []
    metadata_calls = []
    publish_calls = []

    class FakeBackend:
        async def delete(self, path, location, *, scope):
            deletes.append((path, location, scope))

    class FakePolicyService:
        def __init__(self, db):
            self.db = db

        async def delete_metadata(self, **kwargs):
            metadata_calls.append(kwargs)

    class Db:
        def __init__(self):
            self.flushes = 0

        async def flush(self):
            self.flushes += 1

    async def fake_publish_file_change(**kwargs):
        publish_calls.append(kwargs)

    db = Db()
    monkeypatch.setattr(files, "_require_declared_solution_file_location", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(files, "_require_file_policy", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(files, "_install_org_id", lambda *_args, **_kwargs: _async_value(ORG_A))
    monkeypatch.setattr(files, "get_backend", lambda mode, db_arg: FakeBackend())
    monkeypatch.setattr("src.services.file_policy_service.FilePolicyService", FakePolicyService)
    monkeypatch.setattr("src.core.pubsub.publish_file_change", fake_publish_file_change)

    await files.delete_file(
        files.FileDeleteRequest(
            path="folder/report.txt",
            location="reports",
            scope=str(ORG_A),
            mode="cloud",
        ),
        _ctx(),
        SimpleNamespace(user_id=USER_ID),
        db=db,
    )

    assert deletes == [("folder/report.txt", "reports", str(ORG_A))]
    assert publish_calls == [
        {
            "location": "reports",
            "scope": str(ORG_A),
            "path": "folder/report.txt",
            "action": "delete",
        }
    ]
    assert metadata_calls == [
        {
            "organization_id": ORG_A,
            "location": "reports",
            "path": "folder/report.txt",
            "solution_id": None,
        }
    ]
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_delete_file_maps_backend_missing_to_404(monkeypatch):
    class FakeBackend:
        async def delete(self, path, location, *, scope):
            raise FileNotFoundError(path)

    monkeypatch.setattr(files, "_require_declared_solution_file_location", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(files, "_require_file_policy", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(files, "get_backend", lambda mode, db: FakeBackend())

    with pytest.raises(HTTPException) as exc_info:
        await files.delete_file(
            files.FileDeleteRequest(
                path="folder/missing.txt",
                location="reports",
                scope=str(ORG_A),
            ),
            _ctx(),
            SimpleNamespace(user_id=USER_ID),
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "File not found: folder/missing.txt"


@pytest.mark.asyncio
async def test_list_files_simple_filters_each_tier_and_deduplicates(monkeypatch):
    tiers = [
        SimpleNamespace(scope=str(ORG_A), solution_id=None, organization_id=ORG_A),
        SimpleNamespace(scope="global", solution_id=None, organization_id=None),
    ]
    authorize_calls = []
    list_calls = []
    filter_calls = []

    class FakeBackend:
        async def list(self, directory, location, *, scope):
            list_calls.append((directory, location, scope))
            if scope == str(ORG_A):
                return ["reports/keep.txt", "reports/dup.txt", "reports/drop.txt"]
            return ["reports/dup.txt", "reports/global.txt"]

    async def fake_file_read_tiers(db, ctx, location, scope):
        return tiers

    async def fake_authorize(ctx, **kwargs):
        authorize_calls.append(kwargs)
        return kwargs["scope"] == "global"

    async def fake_filter(ctx, *, paths, location, scope, action, solution_id, organization_id):
        filter_calls.append((tuple(paths), location, scope, action, solution_id, organization_id))
        return [path for path in paths if not path.endswith("drop.txt")]

    monkeypatch.setattr("src.services.solution_scope.file_read_tiers", fake_file_read_tiers)
    monkeypatch.setattr(files, "_authorize_file_policy", fake_authorize)
    monkeypatch.setattr(files, "_filter_listed_paths", fake_filter)
    monkeypatch.setattr(files, "get_backend", lambda mode, db: FakeBackend())

    response = await files.list_files_simple(
        files.FileListRequest(directory="reports", location="reports", scope=str(ORG_A)),
        _ctx(),
        SimpleNamespace(user_id=USER_ID),
        db=SimpleNamespace(),
    )

    assert response.files == ["reports/dup.txt", "reports/keep.txt", "reports/global.txt"]
    assert list_calls == [
        ("reports", "reports", str(ORG_A)),
        ("reports", "reports", "global"),
    ]
    assert [call["scope"] for call in authorize_calls] == [str(ORG_A), str(ORG_A), "global"]
    assert len(filter_calls) == 2


@pytest.mark.asyncio
async def test_list_files_simple_returns_workspace_metadata_and_authors(monkeypatch):
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    filter_calls = []

    class FakeRepoStorage:
        async def list_with_metadata(self, directory):
            assert directory == "apps"
            return {
                ".git/config": SimpleNamespace(etag="git", last_modified=now),
                "apps/a.tsx": SimpleNamespace(etag="etag-a", last_modified=now),
                "apps/b.tsx": SimpleNamespace(etag="etag-b", last_modified=now),
            }

    class Result:
        def all(self):
            return [
                SimpleNamespace(path="apps/a.tsx", updated_by="alice@example.test"),
                SimpleNamespace(path="apps/b.tsx", updated_by="bob@example.test"),
            ]

    class Db:
        async def execute(self, _stmt):
            return Result()

    async def fake_file_read_tiers(db, ctx, location, scope):
        return [SimpleNamespace(scope=None, solution_id=None, organization_id=None)]

    async def fake_filter(ctx, *, paths, location, scope, action, solution_id, organization_id):
        filter_calls.append((paths, location, scope, action, solution_id, organization_id))
        return ["apps/b.tsx", "apps/a.tsx"]

    monkeypatch.setattr("src.services.solution_scope.file_read_tiers", fake_file_read_tiers)
    monkeypatch.setattr(files, "_authorize_file_policy", lambda *_args, **_kwargs: _async_value(True))
    monkeypatch.setattr(files, "_filter_listed_paths", fake_filter)
    monkeypatch.setattr("src.services.repo_storage.RepoStorage", lambda: FakeRepoStorage())

    response = await files.list_files_simple(
        files.FileListRequest(
            directory="apps",
            location="workspace",
            include_metadata=True,
            mode="cloud",
        ),
        _ctx(),
        SimpleNamespace(user_id=USER_ID),
        db=Db(),
    )

    assert response.files == ["apps/a.tsx", "apps/b.tsx"]
    assert [item.path for item in response.files_metadata] == ["apps/a.tsx", "apps/b.tsx"]
    assert [item.etag for item in response.files_metadata] == ["etag-a", "etag-b"]
    assert [item.updated_by for item in response.files_metadata] == [
        "alice@example.test",
        "bob@example.test",
    ]
    assert filter_calls[0][0] == ["apps/a.tsx", "apps/b.tsx"]


@pytest.mark.asyncio
async def test_list_files_simple_forbids_when_directory_and_files_denied(monkeypatch):
    async def fake_file_read_tiers(db, ctx, location, scope):
        return [SimpleNamespace(scope=str(ORG_A), solution_id=None, organization_id=ORG_A)]

    class FakeBackend:
        async def list(self, directory, location, *, scope):
            return ["reports/secret.txt"]

    monkeypatch.setattr("src.services.solution_scope.file_read_tiers", fake_file_read_tiers)
    monkeypatch.setattr(files, "_authorize_file_policy", lambda *_args, **_kwargs: _async_value(False))
    monkeypatch.setattr(files, "_filter_listed_paths", lambda *_args, **_kwargs: _async_value([]))
    monkeypatch.setattr(files, "get_backend", lambda mode, db: FakeBackend())

    with pytest.raises(HTTPException) as exc_info:
        await files.list_files_simple(
            files.FileListRequest(directory="reports", location="reports", scope=str(ORG_A)),
            _ctx(),
            SimpleNamespace(user_id=USER_ID),
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["message"] == "File policy denied"
    assert exc_info.value.detail["action"] == "list"
    assert exc_info.value.detail["location"] == "reports"
    assert exc_info.value.detail["path"] == "reports"
    assert exc_info.value.detail["scope"] == str(ORG_A)


@pytest.mark.asyncio
async def test_file_exists_checks_allowed_tiers_until_existing_path(monkeypatch):
    tiers = [
        SimpleNamespace(scope="global", solution_id=None, organization_id=None),
        SimpleNamespace(scope=str(ORG_A), solution_id=None, organization_id=ORG_A),
    ]
    exists_calls = []

    class FakeBackend:
        async def exists(self, path, location, *, scope):
            exists_calls.append((path, location, scope))
            return scope == str(ORG_A)

    async def fake_file_read_tiers(db, ctx, location, scope):
        return tiers

    async def fake_authorize(ctx, **kwargs):
        return kwargs["scope"] == str(ORG_A)

    monkeypatch.setattr("src.services.solution_scope.file_read_tiers", fake_file_read_tiers)
    monkeypatch.setattr(files, "_authorize_file_policy", fake_authorize)
    monkeypatch.setattr(files, "get_backend", lambda mode, db: FakeBackend())

    response = await files.file_exists(
        files.FileExistsRequest(path="reports/q1.txt", location="reports"),
        _ctx(),
        SimpleNamespace(user_id=USER_ID),
        db=SimpleNamespace(),
    )

    assert response.exists is True
    assert exists_calls == [("reports/q1.txt", "reports", str(ORG_A))]


@pytest.mark.asyncio
async def test_pull_files_returns_only_changed_manifest_files(monkeypatch):
    manifest = SimpleNamespace(name="manifest")
    files_by_name = {
        "workflows.yaml": "workflow: changed",
        "tables.yaml": "table: same",
    }
    same_hash = hashlib.sha256(files_by_name["tables.yaml"].encode("utf-8")).hexdigest()

    async def fake_generate_manifest(db):
        assert db is not None
        return manifest

    def fake_serialize_manifest_dir(value):
        assert value is manifest
        return files_by_name

    monkeypatch.setattr("src.services.manifest_generator.generate_manifest", fake_generate_manifest)
    monkeypatch.setattr("bifrost.manifest.serialize_manifest_dir", fake_serialize_manifest_dir)

    response = await files.pull_files(
        files.FilePullRequest(
            prefix="solution-a",
            local_hashes={
                "solution-a/.bifrost/tables.yaml": same_hash,
            },
        ),
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        db=SimpleNamespace(),
    )

    assert response.files == {}
    assert response.deleted == []
    assert response.manifest_files == {"workflows.yaml": "workflow: changed"}


@pytest.mark.asyncio
async def test_pull_files_fails_soft_when_manifest_generation_errors(monkeypatch):
    async def fake_generate_manifest(db):
        raise RuntimeError("manifest unavailable")

    monkeypatch.setattr("src.services.manifest_generator.generate_manifest", fake_generate_manifest)

    response = await files.pull_files(
        files.FilePullRequest(prefix="", local_hashes={}),
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        db=SimpleNamespace(),
    )

    assert response.files == {}
    assert response.deleted == []
    assert response.manifest_files == {}


@pytest.mark.asyncio
async def test_get_manifest_serializes_current_manifest(monkeypatch):
    manifest = SimpleNamespace(name="manifest")

    async def fake_generate_manifest(db):
        assert db == "db"
        return manifest

    def fake_serialize_manifest_dir(value):
        assert value is manifest
        return {"workflows.yaml": "workflow: current"}

    monkeypatch.setattr("src.services.manifest_generator.generate_manifest", fake_generate_manifest)
    monkeypatch.setattr("bifrost.manifest.serialize_manifest_dir", fake_serialize_manifest_dir)

    response = await files.get_manifest(
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        db="db",
    )

    assert response == {"workflows.yaml": "workflow: current"}


@pytest.mark.asyncio
async def test_manage_watch_session_start_heartbeats_and_stop_publish_activity(monkeypatch):
    setex_calls = []
    delete_calls = []
    publish_calls = []

    class FakeRedis:
        async def setex(self, key, ttl, value):
            setex_calls.append((key, ttl, value))

        async def delete(self, key):
            delete_calls.append(key)

    async def fake_get_shared_redis():
        return FakeRedis()

    async def fake_publish_file_activity(**kwargs):
        publish_calls.append(kwargs)

    user = SimpleNamespace(
        user_id=USER_ID,
        name="CLI User",
        email="cli@example.test",
    )
    monkeypatch.setattr("src.core.cache.redis_client.get_shared_redis", fake_get_shared_redis)
    monkeypatch.setattr("src.core.pubsub.publish_file_activity", fake_publish_file_activity)

    assert await files.manage_watch_session(
        files.WatchSessionRequest(action="start", prefix="repo", session_id="s1"),
        user,
    ) == {"ok": True}
    assert await files.manage_watch_session(
        files.WatchSessionRequest(action="heartbeat", prefix="repo", session_id="s1"),
        user,
    ) == {"ok": True}
    assert await files.manage_watch_session(
        files.WatchSessionRequest(action="stop", prefix="repo", session_id="s1"),
        user,
    ) == {"ok": True}

    expected_key = f"bifrost:watch:{USER_ID}:repo"
    assert [call[0] for call in setex_calls] == [expected_key, expected_key]
    assert {call[1] for call in setex_calls} == {files.WATCH_SESSION_TTL_SECONDS}
    assert delete_calls == [expected_key]
    stored = json.loads(setex_calls[0][2])
    assert stored["user_id"] == str(USER_ID)
    assert stored["user_name"] == "CLI User"
    assert stored["prefix"] == "repo"
    assert stored["session_id"] == "s1"
    assert [call["activity_type"] for call in publish_calls] == [
        "watch_start",
        "watch_stop",
    ]


@pytest.mark.asyncio
async def test_list_active_watchers_reads_redis_json_payloads(monkeypatch):
    payload = {
        "user_id": str(USER_ID),
        "user_name": "CLI User",
        "prefix": "repo",
        "session_id": "s1",
    }

    class FakeRedis:
        async def scan_iter(self, pattern):
            assert pattern == "bifrost:watch:*"
            for key in ["watch:1", "watch:2"]:
                yield key

        async def get(self, key):
            if key == "watch:1":
                return json.dumps(payload)
            return None

    async def fake_get_shared_redis():
        return FakeRedis()

    monkeypatch.setattr("src.core.cache.redis_client.get_shared_redis", fake_get_shared_redis)

    response = await files.list_active_watchers(SimpleNamespace(user_id=USER_ID))

    assert response == {"watchers": [payload]}


@pytest.mark.asyncio
async def test_list_files_editor_recursive_filters_excluded_paths(monkeypatch):
    class FakeRepoStorage:
        async def list(self, prefix):
            assert prefix == "src/"
            return [
                "src/workflows/a.py",
                "src/__pycache__/a.pyc",
                "src/apps/app.tsx",
            ]

    monkeypatch.setattr("src.services.repo_storage.RepoStorage", lambda: FakeRepoStorage())
    monkeypatch.setattr(
        "src.services.editor.file_filter.is_excluded_path",
        lambda path: "__pycache__" in path,
    )

    response = await files.list_files_editor(
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        path="src",
        recursive=True,
        db=SimpleNamespace(),
    )

    assert [item.path for item in response] == ["src/apps/app.tsx", "src/workflows/a.py"]
    assert [item.name for item in response] == ["app.tsx", "a.py"]
    assert [item.extension for item in response] == ["tsx", "py"]
    assert {item.type for item in response} == {files.FileType.FILE}


@pytest.mark.asyncio
async def test_list_files_editor_non_recursive_skips_empty_folder_prefixes(monkeypatch):
    list_calls = []

    class FakeRepoStorage:
        async def list_directory(self, prefix):
            assert prefix == ""
            return (["README.md"], ["apps/", "empty/"])

        async def list(self, folder_path):
            list_calls.append(folder_path)
            return ["apps/index.tsx"] if folder_path == "apps/" else []

    monkeypatch.setattr("src.services.repo_storage.RepoStorage", lambda: FakeRepoStorage())

    response = await files.list_files_editor(
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        path=".",
        recursive=False,
        db=SimpleNamespace(),
    )

    assert list_calls == ["apps/", "empty/"]
    assert [(item.path, item.name, item.type, item.extension) for item in response] == [
        ("apps", "apps", files.FileType.FOLDER, None),
        ("README.md", "README.md", files.FileType.FILE, "md"),
    ]


@pytest.mark.asyncio
async def test_get_file_content_editor_returns_text_with_md5_etag(monkeypatch):
    class FakeStorage:
        def __init__(self, db):
            self.db = db

        async def read_file(self, path):
            assert path == "README.md"
            return b"hello", None

    monkeypatch.setattr(files, "FileStorageService", FakeStorage)

    response = await files.get_file_content_editor(
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        path="README.md",
        db=SimpleNamespace(),
    )

    assert response.path == "README.md"
    assert response.content == "hello"
    assert response.encoding == "utf-8"
    assert response.size == 5
    assert response.etag == hashlib.md5(b"hello").hexdigest()


@pytest.mark.asyncio
async def test_get_file_content_editor_base64_encodes_binary_and_maps_missing(monkeypatch):
    class FakeStorage:
        def __init__(self, db):
            self.db = db

        async def read_file(self, path):
            if path == "missing.bin":
                raise FileNotFoundError(path)
            return b"\xff\xfe", None

    monkeypatch.setattr(files, "FileStorageService", FakeStorage)

    binary = await files.get_file_content_editor(
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        path="raw.bin",
        db=SimpleNamespace(),
    )

    assert binary.encoding == "base64"
    assert binary.content == base64.b64encode(b"\xff\xfe").decode("ascii")

    with pytest.raises(HTTPException) as exc_info:
        await files.get_file_content_editor(
            _ctx(is_superuser=True),
            SimpleNamespace(user_id=USER_ID),
            path="missing.bin",
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "File not found: missing.bin"


@pytest.mark.asyncio
async def test_put_file_content_editor_writes_text_and_returns_modified_content(monkeypatch):
    class FakeStorage:
        def __init__(self, db):
            self.db = db

        async def write_file(self, path, content, updated_by, **kwargs):
            assert path == "workflows/a.py"
            assert content == b"print('hi')"
            assert updated_by == "admin@example.test"
            assert kwargs == {
                "force_deactivation": False,
                "replacements": None,
                "workflows_to_deactivate": None,
            }
            return SimpleNamespace(
                final_content=b"print('hi')\n",
                content_modified=True,
                needs_indexing=True,
                workflow_id_conflicts=[
                    SimpleNamespace(
                        name="Existing",
                        function_name="existing",
                        existing_id="workflow-1",
                        file_path="workflows/a.py",
                    )
                ],
                diagnostics=[
                    SimpleNamespace(
                        severity="warning",
                        message="needs indexing",
                        line=1,
                        column=2,
                        source="indexing",
                    )
                ],
                pending_deactivations=[],
                available_replacements=[],
            )

    monkeypatch.setattr(files, "FileStorageService", FakeStorage)

    response = await files.put_file_content_editor(
        files.FileContentRequest(path="workflows/a.py", content="print('hi')"),
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID, email="admin@example.test"),
        db=SimpleNamespace(),
    )

    assert response.content == "print('hi')\n"
    assert response.encoding == "utf-8"
    assert response.size == len(b"print('hi')\n")
    assert response.content_modified is True
    assert response.needs_indexing is True
    assert response.workflow_id_conflicts[0].existing_id == "workflow-1"
    assert response.diagnostics[0].message == "needs indexing"


@pytest.mark.asyncio
async def test_put_file_content_editor_rejects_stale_or_missing_expected_etag(monkeypatch):
    class FakeStorage:
        def __init__(self, db):
            self.db = db

        async def read_file(self, path):
            if path == "missing.py":
                raise FileNotFoundError(path)
            return b"current", None

        async def write_file(self, *_args, **_kwargs):
            raise AssertionError("stale writes should not continue")

    monkeypatch.setattr(files, "FileStorageService", FakeStorage)

    with pytest.raises(HTTPException) as changed:
        await files.put_file_content_editor(
            files.FileContentRequest(
                path="workflows/a.py",
                content="new",
                expected_etag="stale",
            ),
            _ctx(is_superuser=True),
            SimpleNamespace(user_id=USER_ID, email="admin@example.test"),
            db=SimpleNamespace(),
        )

    assert changed.value.status_code == 409
    assert changed.value.detail["reason"] == "content_changed"

    with pytest.raises(HTTPException) as missing:
        await files.put_file_content_editor(
            files.FileContentRequest(
                path="missing.py",
                content="new",
                expected_etag="etag",
            ),
            _ctx(is_superuser=True),
            SimpleNamespace(user_id=USER_ID, email="admin@example.test"),
            db=SimpleNamespace(),
        )

    assert missing.value.status_code == 409
    assert missing.value.detail["reason"] == "path_not_found"


@pytest.mark.asyncio
async def test_put_file_content_editor_returns_pending_deactivation_conflict(monkeypatch):
    class FakeStorage:
        def __init__(self, db):
            self.db = db

        async def write_file(self, *_args, **_kwargs):
            return SimpleNamespace(
                final_content=b"",
                content_modified=False,
                needs_indexing=False,
                workflow_id_conflicts=[],
                diagnostics=[],
                pending_deactivations=[
                    SimpleNamespace(
                        id="workflow-1",
                        name="Old Flow",
                        function_name="old_flow",
                        path="workflows/old.py",
                        description="old",
                        decorator_type="workflow",
                        has_executions=True,
                        last_execution_at="2026-01-01T00:00:00Z",
                        endpoint_enabled=False,
                        affected_entities=[
                            {
                                "entity_type": "agent",
                                "id": "agent-1",
                                "name": "Agent",
                                "reference_type": "tool",
                            }
                        ],
                    )
                ],
                available_replacements=[
                    SimpleNamespace(
                        function_name="new_flow",
                        name="New Flow",
                        decorator_type="workflow",
                        similarity_score=0.9,
                    )
                ],
            )

    monkeypatch.setattr(files, "FileStorageService", FakeStorage)

    with pytest.raises(HTTPException) as exc_info:
        await files.put_file_content_editor(
            files.FileContentRequest(path="workflows/old.py", content=""),
            _ctx(is_superuser=True),
            SimpleNamespace(user_id=USER_ID, email="admin@example.test"),
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["reason"] == "workflows_would_deactivate"
    assert exc_info.value.detail["pending_deactivations"][0]["function_name"] == "old_flow"
    assert exc_info.value.detail["available_replacements"][0]["function_name"] == "new_flow"


@pytest.mark.asyncio
async def test_create_folder_editor_returns_folder_metadata(monkeypatch):
    calls = []

    class FakeStorage:
        def __init__(self, db):
            self.db = db

        async def create_folder(self, path, updated_by):
            calls.append((path, updated_by))

    monkeypatch.setattr(files, "FileStorageService", FakeStorage)

    response = await files.create_folder_editor(
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID, email="admin@example.test"),
        path="apps/new-folder/",
        db=SimpleNamespace(),
    )

    assert calls == [("apps/new-folder/", "admin@example.test")]
    assert response.path == "apps/new-folder"
    assert response.name == "new-folder"
    assert response.type == files.FileType.FOLDER


@pytest.mark.asyncio
async def test_delete_file_editor_deletes_folder_children_and_markers(monkeypatch):
    storage_deletes = []
    repo_deletes = []
    list_calls = []

    class FakeStorage:
        def __init__(self, db):
            self.db = db

        async def delete_file(self, path):
            storage_deletes.append(path)

    class FakeRepo:
        def __init__(self):
            self.calls = 0

        async def list(self, prefix):
            list_calls.append(prefix)
            self.calls += 1
            return ["folder/a.txt", "folder/marker/"] if self.calls == 1 else []

        async def delete(self, path):
            repo_deletes.append(path)

    monkeypatch.setattr(files, "FileStorageService", FakeStorage)
    monkeypatch.setattr("src.services.repo_storage.RepoStorage", FakeRepo)

    await files.delete_file_editor(
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        path="folder",
        db=SimpleNamespace(),
    )

    assert storage_deletes == ["folder/a.txt"]
    assert repo_deletes == ["folder/marker/", "folder", "folder/"]
    assert list_calls == ["folder/", "folder/"]


@pytest.mark.asyncio
async def test_delete_file_editor_deletes_single_file_and_maps_missing(monkeypatch):
    deletes = []

    class FakeStorage:
        def __init__(self, db):
            self.db = db

        async def delete_file(self, path):
            deletes.append(path)
            if path == "missing.txt":
                raise FileNotFoundError(path)

    class FakeRepo:
        async def list(self, prefix):
            return []

    monkeypatch.setattr(files, "FileStorageService", FakeStorage)
    monkeypatch.setattr("src.services.repo_storage.RepoStorage", FakeRepo)

    await files.delete_file_editor(
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        path="file.txt",
        db=SimpleNamespace(),
    )
    assert deletes == ["file.txt"]

    with pytest.raises(HTTPException) as exc_info:
        await files.delete_file_editor(
            _ctx(is_superuser=True),
            SimpleNamespace(user_id=USER_ID),
            path="missing.txt",
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Not found: missing.txt"


@pytest.mark.asyncio
async def test_rename_file_editor_moves_file_and_folder_or_maps_errors(monkeypatch):
    moves = []

    class FakeStorage:
        def __init__(self, db):
            self.db = db

        async def move_file(self, old_path, new_path):
            moves.append((old_path, new_path))
            if old_path == "missing.py":
                raise FileNotFoundError(old_path)
            if new_path == "exists.py":
                raise FileExistsError(new_path)

    monkeypatch.setattr(files, "FileStorageService", FakeStorage)

    file_response = await files.rename_file_editor(
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        old_path="old.py",
        new_path="new.py",
        db=SimpleNamespace(),
    )

    folder_response = await files.rename_file_editor(
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        old_path="apps/old/",
        new_path="apps/new/",
        db=SimpleNamespace(),
    )

    assert file_response.path == "new.py"
    assert file_response.name == "new.py"
    assert file_response.type == files.FileType.FILE
    assert file_response.extension == "py"
    assert folder_response.path == "apps/new/"
    assert folder_response.name == "new"
    assert folder_response.type == files.FileType.FOLDER
    assert folder_response.extension is None

    with pytest.raises(HTTPException) as missing:
        await files.rename_file_editor(
            _ctx(is_superuser=True),
            SimpleNamespace(user_id=USER_ID),
            old_path="missing.py",
            new_path="new.py",
            db=SimpleNamespace(),
        )
    assert missing.value.status_code == 404
    assert missing.value.detail == "Not found: missing.py"

    with pytest.raises(HTTPException) as exists:
        await files.rename_file_editor(
            _ctx(is_superuser=True),
            SimpleNamespace(user_id=USER_ID),
            old_path="old.py",
            new_path="exists.py",
            db=SimpleNamespace(),
        )
    assert exists.value.status_code == 409
    assert exists.value.detail == "Already exists: exists.py"


@pytest.mark.asyncio
async def test_search_file_contents_delegates_and_maps_bad_request(monkeypatch):
    request = SimpleNamespace(query="needle")
    expected = SimpleNamespace(results=[{"path": "a.py"}])
    calls = []

    async def fake_search_files_db(db, request_arg, *, root_path):
        calls.append((db, request_arg, root_path))
        return expected

    monkeypatch.setattr(files, "search_files_db", fake_search_files_db)

    response = await files.search_file_contents(
        request,
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        db="db",
    )

    assert response is expected
    assert calls == [("db", request, "")]

    async def failing_search_files_db(*_args, **_kwargs):
        raise ValueError("bad regex")

    monkeypatch.setattr(files, "search_files_db", failing_search_files_db)

    with pytest.raises(HTTPException) as exc_info:
        await files.search_file_contents(
            request,
            _ctx(is_superuser=True),
            SimpleNamespace(user_id=USER_ID),
            db="db",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "bad regex"


@pytest.mark.asyncio
async def test_list_file_policies_resolves_target_scope_and_serializes_rows(monkeypatch):
    calls = []
    row = SimpleNamespace(
        id=UUID("33333333-3333-3333-3333-333333333333"),
        organization_id=ORG_A,
        location="reports",
        path="exports/",
        policies={"policies": [{"name": "Readers", "actions": ["read"]}]},
    )

    class FakePolicyService:
        def __init__(self, db):
            self.db = db

        async def list_policies(self, **kwargs):
            calls.append(kwargs)
            return [row]

    monkeypatch.setattr(
        "src.services.file_policy_service.FilePolicyService",
        FakePolicyService,
    )

    db = SimpleNamespace()
    response = await files.list_file_policies(
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        location="reports",
        scope=str(ORG_B),
        organization_id=str(ORG_A),
        solution=None,
        db=db,
    )

    assert calls == [{"organization_id": ORG_A, "location": "reports"}]
    assert len(response.policies) == 1
    assert response.policies[0].path == "exports/"
    assert response.policies[0].policies.policies[0].name == "Readers"


@pytest.mark.asyncio
async def test_get_file_policy_decodes_path_and_404s_missing_row(monkeypatch):
    calls = []
    row = SimpleNamespace(
        id=UUID("33333333-3333-3333-3333-333333333333"),
        organization_id=None,
        location="workspace",
        path="folder/report.txt",
        policies={"policies": []},
    )

    class FakePolicyService:
        def __init__(self, db):
            self.db = db

        async def get_policy_exact(self, **kwargs):
            calls.append(kwargs)
            return row if kwargs["path"] == "folder/report.txt" else None

    monkeypatch.setattr(
        "src.services.file_policy_service.FilePolicyService",
        FakePolicyService,
    )

    response = await files.get_file_policy(
        "folder%2Freport.txt",
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        location="workspace",
        scope=None,
        solution=None,
        db=SimpleNamespace(),
    )

    assert response.path == "folder/report.txt"
    assert calls[0] == {
        "organization_id": None,
        "location": "workspace",
        "path": "folder/report.txt",
    }

    with pytest.raises(HTTPException) as exc_info:
        await files.get_file_policy(
            "missing.txt",
            _ctx(is_superuser=True),
            SimpleNamespace(user_id=USER_ID),
            location="workspace",
            scope=None,
            solution=None,
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_set_file_policy_validates_refs_persists_commits_and_publishes(monkeypatch):
    service_calls = []
    publish_calls = []

    row = SimpleNamespace(
        id=UUID("33333333-3333-3333-3333-333333333333"),
        organization_id=ORG_A,
        location="reports",
        path="folder/report.txt",
        policies={"policies": [{"name": "Writers", "actions": ["write"]}]},
    )

    class FakePolicyService:
        def __init__(self, db):
            self.db = db

        async def upsert_policy(self, **kwargs):
            service_calls.append(kwargs)
            return row

    async def fake_resolve_policy_refs(doc, *, repo, action_domain):
        assert action_domain == "file"
        assert doc.policies[0].name == "Writers"
        assert repo is not None

    async def fake_publish_file_policy_changed(**kwargs):
        publish_calls.append(kwargs)

    class Db:
        def __init__(self):
            self.commits = 0

        async def commit(self):
            self.commits += 1

    db = Db()
    monkeypatch.setattr(
        "src.services.file_policy_service.FilePolicyService",
        FakePolicyService,
    )
    monkeypatch.setattr("shared.policy_rules.resolve_policy_refs", fake_resolve_policy_refs)
    monkeypatch.setattr(
        "src.core.pubsub.publish_file_policy_changed",
        fake_publish_file_policy_changed,
    )

    response = await files.set_file_policy(
        "folder%2Freport.txt",
        files.FilePolicySetRequest(
            policies={"policies": [{"name": "Writers", "actions": ["write"]}]}
        ),
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        location="reports",
        scope=str(ORG_A),
        solution=None,
        db=db,
    )

    assert response.path == "folder/report.txt"
    assert service_calls[0]["organization_id"] == ORG_A
    assert service_calls[0]["location"] == "reports"
    assert service_calls[0]["path"] == "folder/report.txt"
    assert service_calls[0]["created_by"] == USER_ID
    assert db.commits == 1
    assert publish_calls == [
        {
            "location": "reports",
            "scope": str(ORG_A),
            "path": "folder/report.txt",
        }
    ]


@pytest.mark.asyncio
async def test_set_file_policy_returns_422_for_unresolvable_policy_ref(monkeypatch):
    from shared.policy_rules import PolicyRuleNotFound

    class FakePolicyService:
        def __init__(self, db):
            self.db = db

        async def upsert_policy(self, **_kwargs):
            raise AssertionError("policy should not be persisted")

    async def fake_resolve_policy_refs(*_args, **_kwargs):
        raise PolicyRuleNotFound("missing rule")

    monkeypatch.setattr(
        "src.services.file_policy_service.FilePolicyService",
        FakePolicyService,
    )
    monkeypatch.setattr("shared.policy_rules.resolve_policy_refs", fake_resolve_policy_refs)

    with pytest.raises(HTTPException) as exc_info:
        await files.set_file_policy(
            "folder",
            files.FilePolicySetRequest(
                policies={"policies": [{"$ref": "missing"}]}
            ),
            _ctx(is_superuser=True),
            SimpleNamespace(user_id=USER_ID),
            location="reports",
            scope=str(ORG_A),
            solution=None,
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 422
    assert "missing rule" in exc_info.value.detail["errors"][0]["message"]


@pytest.mark.asyncio
async def test_delete_file_policy_commits_and_publishes_or_404s(monkeypatch):
    service_calls = []
    publish_calls = []

    class FakePolicyService:
        def __init__(self, db):
            self.db = db

        async def delete_policy(self, **kwargs):
            service_calls.append(kwargs)
            return kwargs["path"] == "folder/report.txt"

    async def fake_publish_file_policy_changed(**kwargs):
        publish_calls.append(kwargs)

    class Db:
        def __init__(self):
            self.commits = 0

        async def commit(self):
            self.commits += 1

    db = Db()
    monkeypatch.setattr(
        "src.services.file_policy_service.FilePolicyService",
        FakePolicyService,
    )
    monkeypatch.setattr(
        "src.core.pubsub.publish_file_policy_changed",
        fake_publish_file_policy_changed,
    )

    await files.delete_file_policy(
        "folder%2Freport.txt",
        _ctx(is_superuser=True),
        SimpleNamespace(user_id=USER_ID),
        location="reports",
        scope=str(ORG_A),
        solution=None,
        db=db,
    )

    assert service_calls[0] == {
        "organization_id": ORG_A,
        "location": "reports",
        "path": "folder/report.txt",
    }
    assert db.commits == 1
    assert publish_calls == [
        {
            "location": "reports",
            "scope": str(ORG_A),
            "path": "folder/report.txt",
        }
    ]

    with pytest.raises(HTTPException) as exc_info:
        await files.delete_file_policy(
            "missing.txt",
            _ctx(is_superuser=True),
            SimpleNamespace(user_id=USER_ID),
            location="reports",
            scope=str(ORG_A),
            solution=None,
            db=db,
        )

    assert exc_info.value.status_code == 404
    assert db.commits == 1


@pytest.mark.asyncio
async def test_filter_listed_paths_uses_relative_policy_paths_and_solution_context(monkeypatch):
    ctx = _ctx(solution_id=SOLUTION_ID)
    calls = []

    async def fake_authorize(
        ctx_arg,
        *,
        action,
        location,
        scope,
        path,
        solution_id,
        organization_id,
        **_kwargs,
    ):
        calls.append(
            {
                "ctx": ctx_arg,
                "action": action,
                "location": location,
                "scope": scope,
                "path": path,
                "solution_id": solution_id,
                "organization_id": organization_id,
            }
        )
        return path.endswith(".txt")

    monkeypatch.setattr(files, "_authorize_file_policy", fake_authorize)

    allowed = await files._filter_listed_paths(
        ctx,
        paths=[
            f"reports/{SOLUTION_ID}/keep.txt",
            f"reports/{SOLUTION_ID}/drop.bin",
        ],
        location="reports",
        scope=str(SOLUTION_ID),
        action="read",
    )

    assert allowed == [f"reports/{SOLUTION_ID}/keep.txt"]
    assert [call["path"] for call in calls] == ["keep.txt", "drop.bin"]
    assert {call["solution_id"] for call in calls} == {SOLUTION_ID}
    assert {call["organization_id"] for call in calls} == {files._USE_CONTEXT_SOLUTION_ID}


@pytest.mark.asyncio
async def test_filter_listed_paths_honors_explicit_solution_and_org_overrides(monkeypatch):
    calls = []

    async def fake_authorize(ctx_arg, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(files, "_authorize_file_policy", fake_authorize)

    allowed = await files._filter_listed_paths(
        _ctx(solution_id=SOLUTION_ID),
        paths=[f"uploads/{ORG_A}/report.pdf"],
        location="uploads",
        scope=str(ORG_A),
        solution_id=None,
        organization_id=ORG_A,
    )

    assert allowed == [f"uploads/{ORG_A}/report.pdf"]
    assert calls == [
        {
            "action": "list",
            "location": "uploads",
            "scope": str(ORG_A),
            "path": "report.pdf",
            "solution_id": None,
            "organization_id": ORG_A,
        }
    ]


@pytest.mark.asyncio
async def test_authorize_file_policy_short_circuits_workspace_and_maps_actions(monkeypatch):
    admin_ctx = _ctx(is_superuser=True)
    user_ctx = _ctx(is_superuser=False)

    assert await files._authorize_file_policy(
        admin_ctx,
        action="read",
        location="workspace",
        scope=None,
        path="README.md",
    ) is True
    assert await files._authorize_file_policy(
        user_ctx,
        action="read",
        location="workspace",
        scope=None,
        path="README.md",
    ) is False

    calls = []

    class FakePolicyService:
        def __init__(self, db):
            self.db = db

        async def is_allowed(self, action, **kwargs):
            calls.append({"action": action, **kwargs})
            return True

    monkeypatch.setattr(
        "src.services.file_policy_service.FilePolicyService",
        FakePolicyService,
    )

    assert await files._authorize_file_policy(
        user_ctx,
        action="signed_put",
        location="uploads",
        scope=str(ORG_A),
        path="report.pdf",
    ) is True

    assert calls == [
        {
            "action": "write",
            "organization_id": ORG_A,
            "location": "uploads",
            "path": "report.pdf",
            "user": user_ctx.user,
            "solution_id": None,
        }
    ]


@pytest.mark.asyncio
async def test_authorize_file_policy_handles_solution_global_and_invalid_scopes(monkeypatch):
    calls = []

    class FakePolicyService:
        def __init__(self, db):
            self.db = db

        async def is_allowed(self, action, **kwargs):
            calls.append({"action": action, **kwargs})
            return True

    monkeypatch.setattr(
        "src.services.file_policy_service.FilePolicyService",
        FakePolicyService,
    )
    monkeypatch.setattr(
        files,
        "_install_org_id",
        lambda ctx, solution_id: _async_value(ORG_B),
    )

    ctx = _ctx(solution_id=SOLUTION_ID, is_superuser=True)

    assert await files._authorize_file_policy(
        ctx,
        action="exists",
        location="reports",
        scope=str(SOLUTION_ID),
        path="owned.txt",
        solution_id=SOLUTION_ID,
    ) is True
    assert await files._authorize_file_policy(
        ctx,
        action="signed_get",
        location="reports",
        scope="global",
        path="global.txt",
    ) is True
    assert await files._authorize_file_policy(
        ctx,
        action="read",
        location="reports",
        scope="not-a-uuid",
        path="bad.txt",
    ) is False
    assert await files._authorize_file_policy(
        ctx,
        action="read",
        location="reports",
        scope=None,
        path="missing-scope.txt",
    ) is False

    assert calls[0]["action"] == "read"
    assert calls[0]["organization_id"] == ORG_B
    assert calls[0]["solution_id"] == SOLUTION_ID
    assert calls[1]["action"] == "read"
    assert calls[1]["organization_id"] is None


@pytest.mark.asyncio
async def test_require_file_policy_raises_for_denied_policy(monkeypatch):
    monkeypatch.setattr(files, "_authorize_file_policy", _async_false)

    with pytest.raises(HTTPException) as exc_info:
        await files._require_file_policy(
            _ctx(),
            action="read",
            location="reports",
            scope=str(ORG_A),
            path="blocked.txt",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["message"] == "File policy denied"
    assert exc_info.value.detail["action"] == "read"
    assert exc_info.value.detail["location"] == "reports"
    assert exc_info.value.detail["path"] == "blocked.txt"
    assert exc_info.value.detail["scope"] == str(ORG_A)


@pytest.mark.asyncio
async def test_test_principal_returns_current_user_without_target_lookup():
    ctx = _ctx()
    db = SimpleNamespace(execute=_async_unexpected)

    assert await files._test_principal(ctx, db, None) is ctx.user


@pytest.mark.asyncio
async def test_test_principal_requires_platform_admin_for_target_user():
    with pytest.raises(HTTPException) as exc_info:
        await files._test_principal(
            _ctx(is_superuser=False),
            SimpleNamespace(),
            str(USER_ID),
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_test_principal_loads_target_user_and_roles(monkeypatch):
    target = SimpleNamespace(
        id=USER_ID,
        email="target@example.test",
        organization_id=ORG_B,
        name="Target User",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        is_external=True,
    )

    class Result:
        def scalar_one_or_none(self):
            return target

    db = SimpleNamespace(execute=lambda _stmt: _async_value(Result()))
    monkeypatch.setattr(
        files,
        "get_user_roles",
        lambda user_id, db_arg: _async_value(([ORG_A], ["Support"])),
    )

    principal = await files._test_principal(_ctx(is_superuser=True), db, str(USER_ID))

    assert principal.user_id == USER_ID
    assert principal.email == "target@example.test"
    assert principal.organization_id == ORG_B
    assert principal.name == "Target User"
    assert principal.is_verified is True
    assert principal.is_external is True
    assert principal.role_ids == [ORG_A]
    assert principal.role_names == ["Support"]


@pytest.mark.asyncio
async def test_test_principal_404s_missing_target_user():
    class Result:
        def scalar_one_or_none(self):
            return None

    db = SimpleNamespace(execute=lambda _stmt: _async_value(Result()))

    with pytest.raises(HTTPException) as exc_info:
        await files._test_principal(_ctx(is_superuser=True), db, str(USER_ID))

    assert exc_info.value.status_code == 404
    assert "User not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_install_org_id_uses_context_org_without_solution_id():
    ctx = _ctx(org_id=ORG_A)

    assert await files._install_org_id(ctx, None) == ORG_A


@pytest.mark.asyncio
async def test_install_org_id_reads_solution_org_and_falls_back_when_missing():
    solution = SimpleNamespace(organization_id=ORG_B)

    class Result:
        def __init__(self, row):
            self.row = row

        def scalar_one_or_none(self):
            return self.row

    class Db:
        def __init__(self):
            self.rows = [solution, None]

        async def execute(self, _stmt):
            return Result(self.rows.pop(0))

    ctx = _ctx(org_id=ORG_A)
    ctx.db = Db()

    assert await files._install_org_id(ctx, SOLUTION_ID) == ORG_B
    assert await files._install_org_id(ctx, SOLUTION_ID) == ORG_A


@pytest.mark.asyncio
async def test_require_declared_solution_file_location_allows_without_solution_id():
    await files._require_declared_solution_file_location(
        _ctx(),
        solution_id=None,
        location="reports",
    )


@pytest.mark.asyncio
async def test_require_declared_solution_file_location_checks_solution_manifest(monkeypatch):
    calls = []

    async def fake_declares(db, solution_id, location):
        calls.append((db, solution_id, location))
        return location == "reports"

    monkeypatch.setattr(
        "src.services.solution_scope.solution_declares_file_location",
        fake_declares,
    )
    ctx = _ctx()

    await files._require_declared_solution_file_location(
        ctx,
        solution_id=SOLUTION_ID,
        location="reports",
    )

    with pytest.raises(HTTPException) as exc_info:
        await files._require_declared_solution_file_location(
            ctx,
            solution_id=SOLUTION_ID,
            location="private",
        )

    assert exc_info.value.status_code == 404
    assert "File location 'private' not found" == exc_info.value.detail
    assert calls == [
        (ctx.db, SOLUTION_ID, "reports"),
        (ctx.db, SOLUTION_ID, "private"),
    ]


async def _async_value(value):
    return value


async def _async_false(*_args, **_kwargs):
    return False


async def _async_unexpected(*_args, **_kwargs):
    raise AssertionError("unexpected async call")
