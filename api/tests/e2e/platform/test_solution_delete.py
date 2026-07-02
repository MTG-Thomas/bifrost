"""E2E coverage for Solution uninstall vs confirmed hard-delete."""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import select

from src.models.orm.config import Config
from src.models.orm.solutions import Solution as SolutionORM
from src.models.orm.tables import Document, Table
from src.models.orm.workflows import Workflow
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


async def test_hard_delete_requires_slug_confirmation(
    e2e_client, platform_admin, db_session
):
    headers = platform_admin.headers
    slug = f"del-confirm-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    r = e2e_client.delete(f"/api/solutions/{sid}", headers=headers)
    assert r.status_code == 422, r.text
    assert "confirm mismatch" in r.json()["detail"]

    assert await db_session.get(SolutionORM, UUID(sid)) is not None


async def test_hard_delete_cascades_owned_entities(
    e2e_client, platform_admin, db_session
):
    headers = platform_admin.headers
    slug = f"del-e2e-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    wf_id = str(uuid.uuid4())
    table_manifest_id = str(uuid.uuid4())
    dep = e2e_client.post(
        f"/api/solutions/{sid}/deploy",
        headers=headers,
        json={
            "python_files": {
                "workflows/w.py": (
                    "from bifrost import workflow\n\n"
                    "@workflow\n"
                    "async def go():\n"
                    "    return 1\n"
                ),
            },
            "workflows": [{
                "id": wf_id,
                "name": f"go_{slug}",
                "function_name": "go",
                "path": "workflows/w.py",
                "type": "workflow",
            }],
            "tables": [{
                "id": table_manifest_id,
                "name": f"customers_{slug}",
                "schema": {"columns": [{"name": "email"}]},
                "policies": None,
            }],
            "config_schemas": [{
                "id": str(uuid.uuid4()),
                "key": f"API_KEY_{uuid.uuid4().hex[:6]}",
                "type": "secret",
                "required": True,
                "description": "needed",
                "position": 0,
            }],
        },
    )
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code == 200, dep.text

    r = e2e_client.delete(
        f"/api/solutions/{sid}",
        headers=headers,
        params={"confirm": slug},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["solution_id"] == sid
    assert body["workflows_deleted"] >= 1
    assert body["tables_deleted"] >= 1
    assert body["config_declarations_deleted"] >= 1

    g = e2e_client.get(f"/api/solutions/{sid}", headers=headers)
    assert g.status_code == 404, g.text

    rows = (
        await db_session.execute(select(Workflow).where(Workflow.solution_id == UUID(sid)))
    ).scalars().all()
    assert rows == []


async def test_uninstall_marks_inactive_without_data_loss(
    e2e_client, platform_admin, db_session
):
    headers = platform_admin.headers
    slug = f"uninst-tbl-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    bundle_tid = str(uuid.uuid4())
    dep = e2e_client.post(
        f"/api/solutions/{sid}/deploy",
        headers=headers,
        json={
            "tables": [{
                "id": bundle_tid,
                "name": f"customers_{slug}",
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
    tbl = (
        await db_session.execute(select(Table).where(Table.id == real_tid))
    ).scalar_one_or_none()
    assert tbl is not None
    assert tbl.solution_id == UUID(sid)

    docs = (
        await db_session.execute(select(Document).where(Document.table_id == real_tid))
    ).scalars().all()
    assert len(docs) == 1
    assert docs[0].data == {"email": "a@b.com"}


async def test_uninstall_leaves_standard_config_values(
    e2e_client, platform_admin, db_session
):
    headers = platform_admin.headers
    slug = f"uninst-cfg-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    key = f"API_KEY_{uuid.uuid4().hex[:6]}"

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


async def test_hard_delete_git_connected_allowed_with_confirm(
    e2e_client, platform_admin, db_session
):
    headers = platform_admin.headers
    sid = uuid.uuid4()
    slug = f"del-git-{uuid.uuid4().hex[:8]}"
    db_session.add(
        SolutionORM(
            id=sid,
            slug=slug,
            name="GIT",
            organization_id=None,
            git_connected=True,
            git_repo_url="https://example.com/repo.git",
        )
    )
    await db_session.commit()

    r = e2e_client.delete(
        f"/api/solutions/{sid}",
        headers=headers,
        params={"confirm": slug},
    )
    assert r.status_code == 200, r.text

    g = e2e_client.get(f"/api/solutions/{sid}", headers=headers)
    assert g.status_code == 404, g.text
