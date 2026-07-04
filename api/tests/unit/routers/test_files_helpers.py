from __future__ import annotations

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
        db=SimpleNamespace(),
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
    assert exc_info.value.detail == "Forbidden"


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
