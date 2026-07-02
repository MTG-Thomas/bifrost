"""Solution-managed rows must not leak into the normal org name cascade.

The solution status lifecycle removed the old orphan provenance columns. A
solution install's owned table remains solution-managed until a hard delete
removes it via cascade, so normal name resolution still excludes it by
``solution_id`` and fetches it explicitly by id when needed.
"""
from __future__ import annotations

import uuid

import pytest

from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.repositories.tables import TableRepository

pytestmark = pytest.mark.e2e


async def _make_org(db) -> uuid.UUID:
    org = Organization(id=uuid.uuid4(), name=f"Org-{uuid.uuid4().hex[:8]}", created_by="dev@x")
    db.add(org)
    await db.flush()
    return org.id


async def _make_solution(db, *, org_id=None) -> uuid.UUID:
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"oc-{uuid.uuid4().hex[:8]}",
        name="OC",
        organization_id=org_id,
    )
    db.add(sol)
    await db.flush()
    return sol.id


def _table(name: str, *, org_id, solution_id=None) -> Table:
    return Table(
        id=uuid.uuid4(),
        name=name,
        organization_id=org_id,
        solution_id=solution_id,
        created_by="dev@x",
        access=None,
    )


class TestSolutionManagedCascadeVisibility:
    async def test_global_solution_row_does_not_leak_into_org_cascade(self, db_session) -> None:
        db = db_session
        org = await _make_org(db)
        solution_id = await _make_solution(db)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        normal = _table(name, org_id=org)
        global_managed = _table(name, org_id=None, solution_id=solution_id)
        db.add_all([normal, global_managed])
        await db.flush()

        repo = TableRepository(session=db, org_id=org, user_id=None, is_superuser=True)
        got = await repo.get(name=name)
        assert got is not None
        assert got.id == normal.id
        assert got.solution_id is None, "name cascade must resolve the _repo table"

    async def test_solution_row_excluded_even_when_only_match(self, db_session) -> None:
        db = db_session
        org = await _make_org(db)
        solution_id = await _make_solution(db, org_id=org)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        managed = _table(name, org_id=org, solution_id=solution_id)
        db.add(managed)
        await db.flush()

        repo = TableRepository(session=db, org_id=org, user_id=None, is_superuser=True)
        assert await repo.get(name=name) is None

    async def test_global_solution_row_excluded_from_org_fallback(self, db_session) -> None:
        db = db_session
        org = await _make_org(db)
        solution_id = await _make_solution(db)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        global_managed = _table(name, org_id=None, solution_id=solution_id)
        db.add(global_managed)
        await db.flush()

        repo = TableRepository(session=db, org_id=org, user_id=None, is_superuser=True)
        assert await repo.get(name=name) is None

    async def test_normal_table_still_resolves(self, db_session) -> None:
        db = db_session
        org = await _make_org(db)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        normal = _table(name, org_id=org)
        db.add(normal)
        await db.flush()

        repo = TableRepository(session=db, org_id=org, user_id=None, is_superuser=True)
        got = await repo.get(name=name)
        assert got is not None and got.id == normal.id

    async def test_solution_managed_table_still_excluded(self, db_session) -> None:
        db = db_session
        org = await _make_org(db)
        solution_id = await _make_solution(db)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        managed = _table(name, org_id=org, solution_id=solution_id)
        db.add(managed)
        await db.flush()

        repo = TableRepository(session=db, org_id=org, user_id=None, is_superuser=True)
        assert await repo.get(name=name) is None

    async def test_solution_row_still_fetchable_by_id(self, db_session) -> None:
        db = db_session
        org = await _make_org(db)
        solution_id = await _make_solution(db, org_id=org)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        managed = _table(name, org_id=org, solution_id=solution_id)
        db.add(managed)
        await db.flush()

        repo = TableRepository(session=db, org_id=org, user_id=None, is_superuser=True)
        got = await repo.get(id=managed.id)
        assert got is not None and got.id == managed.id
