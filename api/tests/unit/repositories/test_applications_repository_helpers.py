from __future__ import annotations

from uuid import uuid4

import pytest

from src.models.orm.applications import Application
from src.repositories.applications import ApplicationRepository


pytestmark = pytest.mark.unit


class _ScalarRows:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class _Session:
    def __init__(self, values=()):
        self.values = list(values)
        self.added = []
        self.deleted = []
        self.flushed = False

    async def execute(self, _stmt):
        return _ScalarRows(self.values)

    def add(self, value):
        self.added.append(value)

    async def delete(self, value):
        self.deleted.append(value)

    async def flush(self):
        self.flushed = True


def _app(slug: str, *, org_id=None) -> Application:
    return Application(
        id=uuid4(),
        name=slug,
        slug=slug,
        repo_path=f"apps/{slug}",
        organization_id=org_id,
        access_level="authenticated",
    )


@pytest.mark.asyncio
async def test_get_by_slug_global_prefers_active_org_then_global_then_stable_pick():
    org_id = uuid4()
    org_app = _app("portal", org_id=org_id)
    global_app = _app("portal", org_id=None)
    other_org_app = _app("portal", org_id=uuid4())

    repo = ApplicationRepository(
        _Session([other_org_app, global_app, org_app]),
        org_id=org_id,
        user_id=None,
        is_superuser=True,
    )

    assert await repo.get_by_slug_global("portal") is org_app

    no_org_repo = ApplicationRepository(
        _Session([other_org_app, global_app]),
        org_id=None,
        user_id=None,
        is_superuser=True,
    )
    assert await no_org_repo.get_by_slug_global("portal") is global_app

    app_a = _app("portal", org_id=uuid4())
    app_b = _app("portal", org_id=uuid4())
    deterministic = ApplicationRepository(
        _Session([app_b, app_a]),
        org_id=uuid4(),
        user_id=None,
        is_superuser=True,
    )
    assert await deterministic.get_by_slug_global("portal") is min(
        [app_a, app_b],
        key=lambda app: str(app.id),
    )


@pytest.mark.asyncio
async def test_get_by_slug_global_returns_none_for_missing_slug():
    repo = ApplicationRepository(_Session([]), org_id=None, user_id=None)

    assert await repo.get_by_slug_global("missing") is None


def test_update_scope_requires_platform_admin_and_accepts_global_or_uuid() -> None:
    app = _app("portal", org_id=uuid4())
    original_org = app.organization_id

    ApplicationRepository._update_scope(app, "global", is_platform_admin=False)
    assert app.organization_id == original_org

    ApplicationRepository._update_scope(app, "global", is_platform_admin=True)
    assert app.organization_id is None

    new_org = uuid4()
    ApplicationRepository._update_scope(app, str(new_org), is_platform_admin=True)
    assert app.organization_id == new_org

    ApplicationRepository._update_scope(app, "not-a-uuid", is_platform_admin=True)
    assert app.organization_id == new_org


@pytest.mark.asyncio
async def test_replace_role_associations_deletes_existing_and_dedupes_new_roles():
    app = _app("portal")
    existing_role = object()
    role_a = uuid4()
    role_b = uuid4()
    session = _Session([existing_role])
    repo = ApplicationRepository(session, org_id=None, user_id=None)

    await repo._replace_role_associations(
        app,
        [role_a, role_b, role_a],
        updated_by="admin@example.test",
    )

    assert session.deleted == [existing_role]
    assert {role.role_id for role in session.added} == {role_a, role_b}
    assert all(role.app_id == app.id for role in session.added)
    assert all(role.assigned_by == "admin@example.test" for role in session.added)


@pytest.mark.asyncio
async def test_replace_role_associations_noops_when_role_ids_absent():
    session = _Session([object()])
    repo = ApplicationRepository(session, org_id=None, user_id=None)

    await repo._replace_role_associations(_app("portal"), None, "admin")

    assert session.deleted == []
    assert session.added == []


class _DeleteRepo(ApplicationRepository):
    def __init__(self, session, result):
        super().__init__(session, org_id=None, user_id=None)
        self.result = result

    async def get(self, id):
        return self.result


@pytest.mark.asyncio
async def test_delete_application_deletes_existing_and_reports_missing():
    app = _app("portal")
    session = _Session()
    repo = _DeleteRepo(session, app)

    assert await repo.delete_application(app.id) is True
    assert session.deleted == [app]
    assert session.flushed is True

    missing_session = _Session()
    missing_repo = _DeleteRepo(missing_session, None)
    assert await missing_repo.delete_application(uuid4()) is False
    assert missing_session.deleted == []
    assert missing_session.flushed is False
