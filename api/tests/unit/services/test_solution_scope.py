from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services import solution_scope


class _ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeDb:
    def __init__(self, *, get_values=None, execute_values=None) -> None:
        self.get_values = list(get_values or [])
        self.execute_values = list(execute_values or [])
        self.get_calls = []
        self.execute_calls = 0

    async def get(self, model, row_id):
        self.get_calls.append((model, row_id))
        return self.get_values.pop(0) if self.get_values else None

    async def execute(self, statement):
        self.execute_calls += 1
        return _ScalarResult(self.execute_values.pop(0) if self.execute_values else None)


def _ctx(*, solution_id=None, app_id=None, org_id=None, is_superuser=False):
    return SimpleNamespace(
        solution_id=str(solution_id) if solution_id is not None else None,
        app_id=str(app_id) if app_id is not None else None,
        org_id=org_id,
        user=SimpleNamespace(
            user_id=uuid4(),
            organization_id=org_id,
            is_superuser=is_superuser,
            is_external=False,
        ),
    )


@pytest.mark.asyncio
async def test_solution_context_id_prefers_valid_solution_id_without_db_lookup() -> None:
    solution_id = uuid4()
    db = _FakeDb(execute_values=[uuid4()])

    assert (
        await solution_scope.solution_context_id(db, _ctx(solution_id=solution_id))
        == solution_id
    )
    assert db.execute_calls == 0


@pytest.mark.asyncio
async def test_solution_context_id_falls_back_to_app_solution_lookup() -> None:
    app_id = uuid4()
    solution_id = uuid4()
    db = _FakeDb(execute_values=[solution_id])

    assert await solution_scope.solution_context_id(db, _ctx(app_id=app_id)) == solution_id
    assert db.execute_calls == 1


@pytest.mark.asyncio
async def test_file_read_tiers_without_solution_uses_workspace_global_scope() -> None:
    tiers = await solution_scope.file_read_tiers(
        _FakeDb(),
        _ctx(org_id=uuid4()),
        location="workspace",
        requested_scope=None,
    )

    assert tiers == [
        solution_scope.FileTier(
            name="global",
            scope="global",
            organization_id=None,
            solution_id=None,
        )
    ]


@pytest.mark.asyncio
async def test_file_read_tiers_for_closed_solution_only_returns_solution_tier() -> None:
    org_id = uuid4()
    solution_id = uuid4()
    solution = SimpleNamespace(
        id=solution_id,
        organization_id=org_id,
        global_repo_access=False,
    )

    tiers = await solution_scope.file_read_tiers(
        _FakeDb(get_values=[solution]),
        _ctx(solution_id=solution_id, org_id=org_id),
        location="files",
        requested_scope=None,
    )

    assert tiers == [
        solution_scope.FileTier(
            name="solution",
            scope=str(solution_id),
            organization_id=org_id,
            solution_id=solution_id,
        )
    ]


@pytest.mark.asyncio
async def test_file_read_tiers_for_open_solution_adds_org_then_global_fallbacks() -> None:
    org_id = uuid4()
    solution_id = uuid4()
    solution = SimpleNamespace(
        id=solution_id,
        organization_id=org_id,
        global_repo_access=True,
    )

    tiers = await solution_scope.file_read_tiers(
        _FakeDb(get_values=[solution]),
        _ctx(solution_id=solution_id, org_id=org_id),
        location="files",
        requested_scope=None,
    )

    assert tiers == [
        solution_scope.FileTier("solution", str(solution_id), org_id, solution_id),
        solution_scope.FileTier("org", str(org_id), org_id, None),
        solution_scope.FileTier("global", "global", None, None),
    ]


@pytest.mark.asyncio
async def test_file_read_tiers_rejects_workspace_inside_solution_context() -> None:
    with pytest.raises(ValueError, match="workspace is not available"):
        await solution_scope.file_read_tiers(
            _FakeDb(),
            _ctx(solution_id=uuid4(), org_id=uuid4()),
            location="workspace",
            requested_scope=None,
        )


@pytest.mark.asyncio
async def test_resolve_solution_table_by_name_returns_own_solution_table_first() -> None:
    solution_id = uuid4()
    org_id = uuid4()
    table = SimpleNamespace(id=uuid4(), name="customers")
    db = _FakeDb(
        get_values=[SimpleNamespace(status="active", global_repo_access=True)],
        execute_values=[table],
    )

    result = await solution_scope.resolve_solution_table_by_name(
        db,
        _ctx(solution_id=solution_id, org_id=org_id),
        "customers",
        target_org_id=org_id,
    )

    assert result is table
    assert db.execute_calls == 1


@pytest.mark.asyncio
async def test_resolve_solution_table_by_name_closed_solution_does_not_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solution_id = uuid4()
    db = _FakeDb(
        get_values=[SimpleNamespace(status="active", global_repo_access=False)],
        execute_values=[None],
    )

    class FailingRepo:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("closed solutions must not use _repo fallback")

    monkeypatch.setattr(solution_scope, "TableRepository", FailingRepo)

    result = await solution_scope.resolve_solution_table_by_name(
        db,
        _ctx(solution_id=solution_id, org_id=uuid4()),
        "customers",
        target_org_id=uuid4(),
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_solution_table_by_name_open_solution_uses_repo_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid4()
    user_id = uuid4()
    fallback = SimpleNamespace(id=uuid4(), name="customers")
    db = _FakeDb(
        get_values=[SimpleNamespace(status="active", global_repo_access=True)],
        execute_values=[None],
    )
    calls = []

    class FakeRepo:
        def __init__(self, db_arg, org_arg, **kwargs) -> None:
            calls.append((db_arg, org_arg, kwargs))

        async def get_by_name(self, name: str):
            calls.append(("get_by_name", name))
            return fallback

    ctx = _ctx(solution_id=uuid4(), org_id=org_id)
    ctx.user.user_id = user_id
    monkeypatch.setattr(solution_scope, "TableRepository", FakeRepo)

    result = await solution_scope.resolve_solution_table_by_name(
        db,
        ctx,
        "customers",
        target_org_id=org_id,
    )

    assert result is fallback
    assert calls == [
        (
            db,
            org_id,
            {
                "user_id": user_id,
                "is_superuser": False,
                "is_external": False,
            },
        ),
        ("get_by_name", "customers"),
    ]
