from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from shared.policy_rules import PolicyRuleDomainMismatch, PolicyRuleNotFound
from src.services import table_policy_loader


def _table(access, *, org_id=None, solution_id=None):
    return SimpleNamespace(
        id=uuid4(),
        access=access,
        organization_id=org_id,
        solution_id=solution_id,
    )


@pytest.mark.asyncio
async def test_empty_access_returns_default_deny_without_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingRepo:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("empty policies should not construct a repository")

    monkeypatch.setattr(table_policy_loader, "PolicyRuleRepository", FailingRepo)

    policies = await table_policy_loader.load_resolved_table_policies(
        _table(None),
        db=object(),
    )

    assert policies.policies == []


@pytest.mark.asyncio
async def test_valid_inline_policy_returns_validated_policies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def resolve_policy_refs(policies, *, repo, action_domain, solution_id):
        calls.append((policies, repo, action_domain, solution_id))

    monkeypatch.setattr(table_policy_loader, "resolve_policy_refs", resolve_policy_refs)

    table = _table(
        {
            "policies": [
                {"name": "readers", "actions": ["read"], "when": None},
            ]
        },
        org_id=uuid4(),
        solution_id=uuid4(),
    )

    policies = await table_policy_loader.load_resolved_table_policies(table, db=object())

    assert [policy.name for policy in policies.policies] == ["readers"]
    assert calls[0][2:] == ("table", table.solution_id)


@pytest.mark.asyncio
async def test_policy_ref_resolution_uses_table_scope_and_solution_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid4()
    solution_id = uuid4()
    db = object()
    repo_calls = []
    resolve_calls = []

    class FakeRepo:
        def __init__(self, db_arg, *, org_id, is_superuser) -> None:
            repo_calls.append((db_arg, org_id, is_superuser))

    async def resolve_policy_refs(policies, *, repo, action_domain, solution_id):
        resolve_calls.append((policies, repo, action_domain, solution_id))

    monkeypatch.setattr(table_policy_loader, "PolicyRuleRepository", FakeRepo)
    monkeypatch.setattr(table_policy_loader, "resolve_policy_refs", resolve_policy_refs)

    table = _table(
        {"policies": [{"$ref": "admin_bypass"}]},
        org_id=org_id,
        solution_id=solution_id,
    )

    policies = await table_policy_loader.load_resolved_table_policies(table, db)

    assert len(policies.policies) == 1
    assert repo_calls == [(db, org_id, True)]
    assert resolve_calls[0][0] is policies
    assert resolve_calls[0][2:] == ("table", solution_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        PolicyRuleNotFound("missing"),
        PolicyRuleDomainMismatch("wrong domain"),
    ],
)
async def test_unresolvable_policy_ref_fails_closed_and_logs_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    exc: Exception,
) -> None:
    async def resolve_policy_refs(*_args, **_kwargs):
        raise exc

    monkeypatch.setattr(table_policy_loader, "resolve_policy_refs", resolve_policy_refs)

    table = _table({"policies": [{"$ref": "missing"}]})
    with caplog.at_level("WARNING", logger="src.services.table_policy_loader"):
        policies = await table_policy_loader.load_resolved_table_policies(
            table,
            db=object(),
        )

    assert policies.policies == []
    assert any("unresolvable policy ref" in rec.message for rec in caplog.records)
