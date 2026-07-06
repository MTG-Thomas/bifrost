"""Unit tests for PolicyRuleService behavior that does not require a database."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.contracts.policy_rule import PolicyRuleCreate, PolicyRuleUpdate
from src.services import policy_rule_service as service_module
from src.services.policy_rule_service import (
    PolicyRuleInUse,
    PolicyRuleNotFoundError,
    PolicyRuleService,
)


class _ScalarResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


def _db_with_execute_results(*rows):
    db = SimpleNamespace()
    db.added = []
    db.deleted = []
    db.add = lambda row: db.added.append(row)
    db.delete = AsyncMock(side_effect=lambda row: db.deleted.append(row))
    db.flush = AsyncMock()
    db.execute = AsyncMock(side_effect=[_ScalarResult(row) for row in rows])
    return db


@pytest.mark.asyncio
async def test_seed_builtin_admin_bypass_adds_only_missing_domains() -> None:
    existing_file_builtin = object()
    db = _db_with_execute_results(None, existing_file_builtin)

    await PolicyRuleService(db).seed_builtin_admin_bypass()

    assert len(db.added) == 1
    [row] = db.added
    assert row.name == "admin_bypass"
    assert row.domain == "file"
    assert row.organization_id is None
    assert row.is_builtin is True
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_persists_rule_and_emits_audit(monkeypatch) -> None:
    db = _db_with_execute_results()
    emitted = AsyncMock()
    monkeypatch.setattr(service_module, "emit_audit", emitted)
    actor = object()
    org_id = uuid4()

    row = await PolicyRuleService(db).create(
        PolicyRuleCreate(
            name="ops",
            domain="file",
            organization_id=org_id,
            description="Operations files",
            body={"actions": ["read"], "when": None},
        ),
        actor=actor,
    )

    assert row in db.added
    assert row.name == "ops"
    assert row.domain == "file"
    assert row.organization_id == org_id
    assert row.body == {"actions": ["read"], "when": None}
    db.flush.assert_awaited_once()
    emitted.assert_awaited_once_with(
        db,
        "policy_rule.create",
        resource_type="policy_rule",
        resource_id=row.id,
        details={"name": "ops", "domain": "file"},
        actor_override=actor,
    )


@pytest.mark.asyncio
async def test_update_renames_cascades_updates_body_and_audits(monkeypatch) -> None:
    db = _db_with_execute_results()
    service = PolicyRuleService(db)
    org_id = uuid4()
    row = SimpleNamespace(
        id=uuid4(),
        organization_id=org_id,
        solution_id=None,
        name="ops",
        domain="file",
        description="old",
        body={"actions": ["read"], "when": None},
        is_builtin=False,
    )
    usages = SimpleNamespace(file_policies=[{"id": "fp1"}], tables=[], total=1)
    service._get = AsyncMock(return_value=row)
    monkeypatch.setattr(service_module, "find_policy_rule_usages", AsyncMock(return_value=usages))
    emitted = AsyncMock()
    monkeypatch.setattr(service_module, "emit_audit", emitted)
    actor = object()

    updated = await service.update(
        "ops",
        "file",
        PolicyRuleUpdate(
            name="operations",
            description="new",
            body={"actions": ["read", "write"], "when": None},
        ),
        org_id=org_id,
        actor=actor,
    )

    assert updated is row
    assert row.name == "operations"
    assert row.description == "new"
    assert row.body == {"actions": ["read", "write"], "when": None}
    db.flush.assert_awaited_once()
    emitted.assert_awaited_once_with(
        db,
        "policy_rule.update",
        resource_type="policy_rule",
        resource_id=row.id,
        details={
            "name": "operations",
            "domain": "file",
            "renamed_to": "operations",
            "usages": 1,
        },
        actor_override=actor,
    )


@pytest.mark.asyncio
async def test_delete_raises_in_use_without_deleting_or_auditing(monkeypatch) -> None:
    db = _db_with_execute_results()
    service = PolicyRuleService(db)
    org_id = uuid4()
    row = SimpleNamespace(
        organization_id=org_id,
        solution_id=None,
        name="ops",
        domain="file",
        is_builtin=False,
    )
    usages = SimpleNamespace(file_policies=[{"id": "fp1"}], tables=[], total=1)
    service._get = AsyncMock(return_value=row)
    monkeypatch.setattr(service_module, "find_policy_rule_usages", AsyncMock(return_value=usages))
    emitted = AsyncMock()
    monkeypatch.setattr(service_module, "emit_audit", emitted)

    with pytest.raises(PolicyRuleInUse) as exc:
        await service.delete("ops", "file", org_id=org_id, actor=object())

    assert exc.value.usages is usages
    db.delete.assert_not_awaited()
    db.flush.assert_not_awaited()
    emitted.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_raises_not_found_after_default_and_solution_managed_lookup_miss(
    monkeypatch,
) -> None:
    db = _db_with_execute_results()
    repo = SimpleNamespace(get=AsyncMock(side_effect=[None, None]))

    class FakePolicyRuleRepository:
        def __init__(self, db_arg, *, org_id, is_superuser):
            assert db_arg is db
            assert org_id == "org-1"
            assert is_superuser is True

        async def get(self, **kwargs):
            return await repo.get(**kwargs)

    monkeypatch.setattr(service_module, "PolicyRuleRepository", FakePolicyRuleRepository)

    with pytest.raises(PolicyRuleNotFoundError):
        await PolicyRuleService(db)._get("ops", "file", "org-1")

    assert repo.get.await_args_list[0].kwargs == {"name": "ops", "domain": "file"}
    assert repo.get.await_args_list[1].kwargs == {
        "name": "ops",
        "domain": "file",
        "include_solution_managed": True,
    }
