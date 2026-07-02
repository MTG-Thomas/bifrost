"""Compatibility coverage after retiring table/config orphan provenance columns."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from src.models.enums import ConfigType as ConfigTypeEnum
from src.models.orm.config import Config
from src.models.orm.tables import Table

pytestmark = pytest.mark.e2e


async def test_tables_list_accepts_legacy_include_orphaned_query(
    e2e_client, platform_admin, db_session
):
    headers = platform_admin.headers
    name = f"normal_{uuid.uuid4().hex[:8]}"

    table = Table(
        id=uuid.uuid4(),
        name=name,
        organization_id=None,
        created_by="dev@x",
        access=None,
    )
    db_session.add(table)
    await db_session.commit()

    r = e2e_client.get("/api/tables?include_orphaned=true", headers=headers)
    assert r.status_code == 200, r.text
    by_name = {t["name"]: t for t in r.json()["tables"]}
    assert name in by_name
    assert "orphaned_at" not in by_name[name]
    assert "origin_solution_slug" not in by_name[name]
    assert "origin_solution_id" not in by_name[name]


async def test_configs_list_accepts_legacy_include_orphaned_query(
    e2e_client, platform_admin, db_session
):
    headers = platform_admin.headers
    key = f"normal_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    config = Config(
        id=uuid.uuid4(),
        key=key,
        value={"value": "v"},
        config_type=ConfigTypeEnum.STRING,
        organization_id=None,
        created_at=now,
        updated_at=now,
        updated_by="dev@x",
    )
    db_session.add(config)
    await db_session.commit()

    r = e2e_client.get("/api/config?include_orphaned=true", headers=headers)
    assert r.status_code == 200, r.text
    by_key = {c["key"]: c for c in r.json()}
    assert key in by_key
    assert "orphaned_at" not in by_key[key]
    assert "origin_solution_slug" not in by_key[key]
    assert "origin_solution_id" not in by_key[key]
