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
