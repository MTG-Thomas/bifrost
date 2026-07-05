"""Pre-resolving claim references from table policies."""

from types import SimpleNamespace
from typing import Any, Literal
from uuid import uuid4

import pytest

from src.models.contracts.claims import ClaimQuery, CustomClaim
from src.models.contracts.policies import TablePolicies


def _claim(name: str, *, organization_id=None, solution_id=None, table="memberships") -> CustomClaim:
    return CustomClaim(
        id=uuid4(),
        organization_id=organization_id or uuid4(),
        solution_id=solution_id,
        name=name,
        type="list",
        query=ClaimQuery(table=table, select="campus_id"),
    )


def _claim_with_query(
    name: str,
    *,
    claim_type: Literal["list", "scalar"] = "list",
    where: dict[str, Any] | None = None,
    select: str = "campus_id",
) -> CustomClaim:
    return CustomClaim(
        id=uuid4(),
        organization_id=uuid4(),
        solution_id=None,
        name=name,
        type=claim_type,
        query=ClaimQuery(table="memberships", select=select, where=where),
    )


@pytest.mark.asyncio
async def test_preresolve_resolves_each_referenced_claim_once(monkeypatch):
    from shared.claims import preresolve

    org_id = uuid4()
    claims = {
        "allowed_campus_ids": _claim("allowed_campus_ids"),
        "allowed_doc_type_ids": _claim("allowed_doc_type_ids"),
    }
    resolved: list[str] = []

    async def fake_load(db, loaded_org_id, loaded_solution_id=None):
        assert loaded_org_id == org_id
        assert loaded_solution_id is None
        return claims

    async def fake_resolve(claim, all_claims, user, db, resolving):
        resolved.append(claim.name)

    monkeypatch.setattr(preresolve, "_load_claims", fake_load)
    monkeypatch.setattr(preresolve, "_resolve_claim", fake_resolve)

    policies = TablePolicies.model_validate({
        "policies": [
            {
                "name": "scoped_read",
                "actions": ["read"],
                "when": {
                    "and": [
                        {
                            "in": [
                                {"row": "campus_id"},
                                {"claims": "allowed_campus_ids"},
                            ]
                        },
                        {
                            "in": [
                                {"row": "doc_type_id"},
                                {"claims": "allowed_doc_type_ids"},
                            ]
                        },
                        {
                            "in": [
                                {"row": "campus_id"},
                                {"claims": "allowed_campus_ids"},
                            ]
                        },
                    ]
                },
            }
        ]
    })

    await preresolve.preresolve_for_policies(
        SimpleNamespace(),
        policies,
        db=None,  # type: ignore[arg-type]
        org_id=org_id,
    )

    assert set(resolved) == {"allowed_campus_ids", "allowed_doc_type_ids"}
    assert len(resolved) == 2


@pytest.mark.asyncio
async def test_preresolve_noops_when_no_claim_refs(monkeypatch):
    from shared.claims import preresolve

    async def fail_load(db, org_id, solution_id=None):
        raise AssertionError("claims should not be loaded")

    monkeypatch.setattr(preresolve, "_load_claims", fail_load)
    policies = TablePolicies.model_validate({
        "policies": [
            {
                "name": "own_row",
                "actions": ["read"],
                "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
            }
        ]
    })

    await preresolve.preresolve_for_policies(
        SimpleNamespace(),
        policies,
        db=None,  # type: ignore[arg-type]
        org_id=uuid4(),
    )


@pytest.mark.e2e
async def test_load_claims_prefers_solution_claim_over_repo_claim(db_session):
    from shared.claims import preresolve
    from src.models.orm.custom_claims import CustomClaim as CustomClaimORM
    from src.models.orm.organizations import Organization
    from src.models.orm.solutions import Solution

    org = Organization(id=uuid4(), name=f"ClaimsOrg-{uuid4().hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()
    sol = Solution(
        id=uuid4(),
        slug=f"claims-{uuid4().hex[:8]}",
        name="Claims",
        organization_id=org.id,
    )
    db_session.add(sol)
    await db_session.flush()
    db_session.add_all([
        CustomClaimORM(
            id=uuid4(),
            organization_id=org.id,
            solution_id=None,
            name="allowed_campus_ids",
            type="list",
            query={"table": "repo_memberships", "select": "campus_id"},
        ),
        CustomClaimORM(
            id=uuid4(),
            organization_id=org.id,
            solution_id=sol.id,
            name="allowed_campus_ids",
            type="list",
            query={"table": "solution_memberships", "select": "campus_id"},
        ),
    ])
    await db_session.flush()

    claims = await preresolve._load_claims(db_session, org.id, sol.id)

    assert claims["allowed_campus_ids"].solution_id == sol.id
    assert claims["allowed_campus_ids"].query.table == "solution_memberships"


@pytest.mark.asyncio
async def test_run_claim_query_returns_empty_when_source_table_denies_read(monkeypatch):
    """Regression: claims must NOT bypass the source table's read filter.

    If `compile_read_filter` returns None for the source table (no rule grants
    read to the caller), the claim resolves to [] without touching Document.
    """
    from shared.claims import preresolve
    from src.models.contracts.policies import TablePolicies

    source = SimpleNamespace(id=uuid4(), access=None)

    class _FakeResult:
        def scalar_one_or_none(self):
            return source

        def scalars(self):
            raise AssertionError(
                "Document scan must not happen when source table denies read"
            )

    class _FakeDB:
        async def execute(self, _stmt):
            return _FakeResult()

    # Source table has no read-granting policies → compile_read_filter is None.
    async def _fake_load_source_policies(_s, _db):
        return TablePolicies()

    monkeypatch.setattr(preresolve, "_load_source_policies", _fake_load_source_policies)

    claim = _claim("locked")
    rows = await preresolve._run_claim_query(
        claim,
        claims={},
        user=SimpleNamespace(claims={}),
        db=_FakeDB(),  # type: ignore[arg-type]
        resolving=set(),
    )
    assert rows == []


def test_get_or_init_cache_reuses_existing_claims_dict() -> None:
    from shared.claims import preresolve

    claims = {"team_ids": ["north"]}
    user = SimpleNamespace(claims=claims)

    assert preresolve._get_or_init_cache(user) is claims


def test_get_or_init_cache_creates_claims_dict_on_user() -> None:
    from shared.claims import preresolve

    user = SimpleNamespace()

    cache = preresolve._get_or_init_cache(user)

    assert cache == {}
    assert user.claims is cache


@pytest.mark.asyncio
async def test_resolve_claim_preresolves_dependencies_and_caches_result(monkeypatch) -> None:
    from shared.claims import preresolve

    dependent = _claim_with_query("allowed_campus_ids")
    parent = _claim_with_query(
        "allowed_room_ids",
        where={"in": [{"row": "campus_id"}, {"claims": "allowed_campus_ids"}]},
        select="room_id",
    )
    calls: list[str] = []

    async def fake_run_claim_query(claim, _claims, _user, _db, _resolving):
        calls.append(claim.name)
        return [{claim.query.select: f"{claim.name}-value"}]

    monkeypatch.setattr(preresolve, "_run_claim_query", fake_run_claim_query)
    user = SimpleNamespace(claims={})
    claims = {
        dependent.name: dependent,
        parent.name: parent,
    }

    first = await preresolve._resolve_claim(parent, claims, user, db=None, resolving=set())
    second = await preresolve._resolve_claim(parent, claims, user, db=None, resolving=set())

    assert first == ["allowed_room_ids-value"]
    assert second == first
    assert user.claims == {
        "allowed_campus_ids": ["allowed_campus_ids-value"],
        "allowed_room_ids": ["allowed_room_ids-value"],
    }
    assert calls == ["allowed_campus_ids", "allowed_room_ids"]


@pytest.mark.asyncio
async def test_resolve_claim_cycle_fails_closed_by_claim_type() -> None:
    from shared.claims import preresolve

    list_claim = _claim_with_query("allowed_campus_ids", claim_type="list")
    scalar_claim = _claim_with_query("primary_campus_id", claim_type="scalar")
    user = SimpleNamespace(claims={})

    assert await preresolve._resolve_claim(
        list_claim, {list_claim.name: list_claim}, user, db=None, resolving={list_claim.name}
    ) == []
    assert await preresolve._resolve_claim(
        scalar_claim,
        {scalar_claim.name: scalar_claim},
        user,
        db=None,
        resolving={scalar_claim.name},
    ) is None
    assert user.claims == {"allowed_campus_ids": [], "primary_campus_id": None}


def test_read_policy_claim_deps_only_uses_read_policy_claim_refs() -> None:
    from shared.claims import preresolve

    policies = TablePolicies.model_validate({
        "policies": [
            {
                "name": "read_by_claim",
                "actions": ["read"],
                "when": {"in": [{"row": "campus_id"}, {"claims": "allowed_campus_ids"}]},
            },
            {
                "name": "update_by_claim",
                "actions": ["update"],
                "when": {"in": [{"row": "campus_id"}, {"claims": "editor_campus_ids"}]},
            },
            {"name": "read_unconditional", "actions": ["read"]},
        ]
    })

    assert preresolve._read_policy_claim_deps(policies) == {"allowed_campus_ids"}


def test_extract_reads_document_columns_and_nested_json_paths() -> None:
    from shared.claims import preresolve

    doc_id = uuid4()
    table_id = uuid4()
    row = SimpleNamespace(
        id=doc_id,
        table_id=table_id,
        created_by="creator",
        updated_by="updater",
        data={"site": {"name": "HQ"}, "plain": "value", "empty": None},
    )

    assert preresolve._extract(row, "id") == doc_id
    assert preresolve._extract(row, "table_id") == table_id
    assert preresolve._extract(row, "site.name") == "HQ"
    assert preresolve._extract(row, "plain") == "value"
    assert preresolve._extract(row, "site.missing") is None
    assert preresolve._extract(row, "plain.child") is None
    assert preresolve._extract(SimpleNamespace(data=None), "site.name") is None


@pytest.mark.asyncio
async def test_load_source_policies_fails_closed_on_malformed_access() -> None:
    from shared.claims import preresolve

    source = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        access={"policies": [{"name": "", "actions": ["read"]}]},
    )

    policies = await preresolve._load_source_policies(source, db=None)  # type: ignore[arg-type]

    assert policies == TablePolicies()


@pytest.mark.asyncio
async def test_load_source_policies_fails_closed_on_unresolved_rule_ref(monkeypatch) -> None:
    from shared.claims import preresolve
    from shared.policy_rules import PolicyRuleNotFound

    async def fail_resolve_policy_refs(*_args, **_kwargs):
        raise PolicyRuleNotFound("missing")

    monkeypatch.setattr(preresolve, "resolve_policy_refs", fail_resolve_policy_refs)
    source = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        access={"policies": [{"$ref": "table-read"}]},
    )

    policies = await preresolve._load_source_policies(source, db=None)  # type: ignore[arg-type]

    assert policies == TablePolicies()
