"""E2E regressions for the post-orphan Solution status lifecycle."""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import select

from src.models.orm.config import Config
from src.models.orm.tables import Document, Table
from src.services.solutions.deploy import solution_entity_id
from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug.upper(), "organization_id": None},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_uninstall_preserves_solution_table_with_data(
    e2e_client, platform_admin, db_session
):
    headers = platform_admin.headers
    slug = f"status-tbl-{uuid.uuid4().hex[:8]}"
    table_name = f"customers_{slug}"
    sid = _create_solution(e2e_client, headers, slug)

    bundle_tid = str(uuid.uuid4())
    dep = e2e_client.post(
        f"/api/solutions/{sid}/deploy",
        headers=headers,
        json={
            "tables": [{
                "id": bundle_tid,
                "name": table_name,
                "description": "customer records",
                "schema": {"columns": [{"name": "email"}]},
                "policies": None,
            }],
        },
    )
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code == 200, dep.text
    real_tid = solution_entity_id(UUID(sid), UUID(bundle_tid))

    doc = e2e_client.post(
        f"/api/tables/{real_tid}/documents",
        headers=headers,
        json={"id": "row-1", "data": {"email": "a@b.com"}},
    )
    assert doc.status_code in (200, 201), doc.text

    r = e2e_client.post(f"/api/solutions/{sid}/uninstall", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "inactive"

    db_session.expire_all()
    table = (
        await db_session.execute(select(Table).where(Table.id == real_tid))
    ).scalar_one_or_none()
    assert table is not None
    assert table.solution_id == UUID(sid)

    docs = (
        await db_session.execute(select(Document).where(Document.table_id == real_tid))
    ).scalars().all()
    assert len(docs) == 1
    assert docs[0].data == {"email": "a@b.com"}


async def test_uninstall_preserves_standard_config_value(
    e2e_client, platform_admin, db_session
):
    headers = platform_admin.headers
    slug = f"status-cfg-{uuid.uuid4().hex[:8]}"
    key = f"API_KEY_{uuid.uuid4().hex[:6]}"
    sid = _create_solution(e2e_client, headers, slug)

    dep = e2e_client.post(
        f"/api/solutions/{sid}/deploy",
        headers=headers,
        json={
            "config_schemas": [{
                "id": str(uuid.uuid4()),
                "key": key,
                "type": "string",
                "required": True,
                "description": "needed",
                "position": 0,
            }],
        },
    )
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code == 200, dep.text

    sc = e2e_client.post(
        "/api/config",
        headers=headers,
        json={"key": key, "value": "sekret", "type": "string", "organization_id": None},
    )
    assert sc.status_code in (200, 201), sc.text

    r = e2e_client.post(f"/api/solutions/{sid}/uninstall", headers=headers)
    assert r.status_code == 200, r.text

    db_session.expire_all()
    cfg = (
        await db_session.execute(
            select(Config).where(Config.key == key, Config.organization_id.is_(None))
        )
    ).scalar_one_or_none()
    assert cfg is not None
    assert cfg.value["value"] == "sekret"


async def test_fresh_install_has_no_orphan_provenance_columns(
    e2e_client, platform_admin, db_session
):
    headers = platform_admin.headers
    slug = f"fresh-tbl-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    bundle_tid = str(uuid.uuid4())
    real_tid = solution_entity_id(UUID(sid), UUID(bundle_tid))

    dep = e2e_client.post(
        f"/api/solutions/{sid}/deploy",
        headers=headers,
        json={
            "tables": [{
                "id": bundle_tid,
                "name": f"things_{slug}",
                "schema": {"columns": [{"name": "x"}]},
                "policies": None,
            }],
        },
    )
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code == 200, dep.text

    db_session.expire_all()
    tbl = (
        await db_session.execute(select(Table).where(Table.id == real_tid))
    ).scalar_one_or_none()
    assert tbl is not None
    assert tbl.solution_id == UUID(sid)
    assert not hasattr(tbl, "orphaned_at")
    assert not hasattr(tbl, "origin_solution_slug")
    assert not hasattr(tbl, "origin_solution_id")

    docs = (
        await db_session.execute(select(Document).where(Document.table_id == real_tid))
    ).scalars().all()
    assert docs == []
