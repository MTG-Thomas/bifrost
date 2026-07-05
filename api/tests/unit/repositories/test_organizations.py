import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from src.models import Organization
from src.repositories.base import BaseRepository
from src.repositories.organizations import OrganizationRepository


ORG_ID = UUID("11111111-1111-1111-1111-111111111111")


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _ExecuteResult:
    def __init__(self, *, scalar=None, scalars=None):
        self._scalar = scalar
        self._scalars = scalars or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return _ScalarResult(self._scalars)


class _Session:
    def __init__(self, *results):
        self._results = list(results)
        self.statements = []
        self.add = MagicMock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.delete = AsyncMock()

    async def execute(self, statement):
        self.statements.append(statement)
        return self._results.pop(0)


class _Repo(BaseRepository):
    model = Organization


@pytest.mark.asyncio
async def test_base_repository_crud_methods_use_session_primitives():
    entity_id = uuid4()
    entity = SimpleNamespace(id=entity_id)
    session = _Session(
        _ExecuteResult(scalar=entity),
        _ExecuteResult(scalars=[entity]),
    )
    repo = _Repo(session)

    assert await repo.get_by_id(entity_id) is entity
    assert await repo.get_all(limit=5, offset=10) == [entity]
    assert await repo.create(entity) is entity
    assert await repo.update(entity) is entity
    await repo.delete(entity)

    assert len(session.statements) == 2
    session.add.assert_called_once_with(entity)
    assert session.flush.await_count == 3
    assert session.refresh.await_args_list[0].args == (entity,)
    assert session.refresh.await_args_list[1].args == (entity,)
    session.delete.assert_awaited_once_with(entity)


@pytest.mark.asyncio
async def test_base_repository_delete_by_id_returns_false_when_missing():
    session = _Session(_ExecuteResult(scalar=None))
    repo = _Repo(session)

    assert await repo.delete_by_id(uuid4()) is False
    session.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_base_repository_delete_by_id_deletes_found_entity():
    entity = SimpleNamespace(id=uuid4())
    session = _Session(_ExecuteResult(scalar=entity))
    repo = _Repo(session)

    assert await repo.delete_by_id(entity.id) is True
    session.delete.assert_awaited_once_with(entity)


@pytest.mark.asyncio
async def test_get_with_cache_accepts_legacy_scope_id_and_returns_sdk_org():
    session = _Session()
    repo = OrganizationRepository(session)
    repo._get_from_cache = AsyncMock(
        return_value={
            "id": str(ORG_ID),
            "name": "Midtown",
            "is_active": True,
            "is_provider": True,
        }
    )

    org = await repo.get_with_cache(f"ORG:{ORG_ID}")

    assert org is not None
    assert org.id == str(ORG_ID)
    assert org.name == "Midtown"
    assert org.is_active is True
    assert org.is_provider is True
    repo._get_from_cache.assert_awaited_once_with(str(ORG_ID))
    assert session.statements == []


@pytest.mark.asyncio
async def test_get_with_cache_rejects_invalid_uuid_without_querying(caplog):
    session = _Session()
    repo = OrganizationRepository(session)

    assert await repo.get_with_cache("not-a-uuid") is None

    assert "Invalid organization ID format" in caplog.text
    assert session.statements == []


@pytest.mark.asyncio
async def test_get_with_cache_loads_db_and_populates_cache_on_miss():
    entity = SimpleNamespace(
        id=ORG_ID,
        name="Midtown",
        domain="midtowntg.com",
        is_active=True,
        is_provider=False,
    )
    session = _Session(_ExecuteResult(scalar=entity))
    repo = OrganizationRepository(session)
    repo._get_from_cache = AsyncMock(return_value=None)
    repo._set_cache = AsyncMock()

    org = await repo.get_with_cache(str(ORG_ID))

    assert org is not None
    assert org.id == str(ORG_ID)
    assert org.name == "Midtown"
    repo._set_cache.assert_awaited_once_with(
        org_id=str(ORG_ID),
        name="Midtown",
        domain="midtowntg.com",
        is_active=True,
        is_provider=False,
    )


@pytest.mark.asyncio
async def test_get_with_cache_returns_none_for_missing_db_row():
    session = _Session(_ExecuteResult(scalar=None))
    repo = OrganizationRepository(session)
    repo._get_from_cache = AsyncMock(return_value=None)
    repo._set_cache = AsyncMock()

    assert await repo.get_with_cache(str(ORG_ID)) is None
    repo._set_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_helpers_round_trip_json_and_swallow_cache_errors(monkeypatch):
    redis = AsyncMock()
    redis.get.side_effect = [
        json.dumps({"id": str(ORG_ID), "name": "Midtown"}).encode(),
        RuntimeError("redis down"),
    ]
    monkeypatch.setattr(
        "src.core.cache.get_shared_redis",
        AsyncMock(return_value=redis),
    )
    monkeypatch.setattr("src.core.cache.org_key", lambda org_id: f"org:{org_id}")
    monkeypatch.setattr("src.core.cache.TTL_ORGS", 60)
    repo = OrganizationRepository(_Session())

    assert await repo._get_from_cache(str(ORG_ID)) == {
        "id": str(ORG_ID),
        "name": "Midtown",
    }
    assert await repo._get_from_cache(str(ORG_ID)) is None
    await repo._set_cache(str(ORG_ID), "Midtown", "midtowntg.com", True, True)

    redis.set.assert_awaited_once()
    key, payload = redis.set.await_args.args[:2]
    assert key == f"org:{ORG_ID}"
    assert json.loads(payload)["is_provider"] is True


@pytest.mark.asyncio
async def test_get_by_domain_lowercases_domain_and_get_active_returns_list():
    active_org = SimpleNamespace(name="Midtown")
    domain_org = SimpleNamespace(domain="midtowntg.com")
    session = _Session(
        _ExecuteResult(scalar=domain_org),
        _ExecuteResult(scalars=[active_org]),
    )
    repo = OrganizationRepository(session)

    assert await repo.get_by_domain("MidtownTG.COM") is domain_org
    assert await repo.get_active(limit=10, offset=20) == [active_org]

    assert len(session.statements) == 2
    assert "midtowntg.com" in session.statements[0].compile().params.values()


@pytest.mark.asyncio
async def test_create_organization_normalizes_domain_before_create(monkeypatch):
    created = {}
    repo = OrganizationRepository(_Session())

    async def fake_create(org):
        created["org"] = org
        return org

    monkeypatch.setattr(repo, "create", fake_create)

    org = await repo.create_organization(
        "Midtown",
        created_by="tester",
        domain="MidtownTG.COM",
    )

    assert org is created["org"]
    assert org.name == "Midtown"
    assert org.domain == "midtowntg.com"
    assert org.created_by == "tester"
    assert org.is_active is True
