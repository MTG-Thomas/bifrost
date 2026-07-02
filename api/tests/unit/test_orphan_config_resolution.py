"""Config resolution follows normal scoped values after orphan column removal."""

from uuid import uuid4

import pytest

from src.models.contracts.config import SetConfigRequest
from src.models.orm.config import Config, ConfigType
from src.models.orm.organizations import Organization
from src.repositories.config import ConfigRepository


async def _make_org(db) -> Organization:
    org = Organization(id=uuid4(), name=f"Org-{uuid4().hex[:6]}", created_by="op@test")
    db.add(org)
    await db.flush()
    return org


async def _set_value(db, org_id, key, value) -> None:
    repo = ConfigRepository(db, org_id=org_id, is_superuser=True)
    await repo.set_config(
        SetConfigRequest(
            key=key, value=value, type=ConfigType.STRING, organization_id=org_id
        ),
        updated_by="op@test",
    )
    await db.flush()


def test_config_model_has_no_orphan_provenance_attributes() -> None:
    assert not hasattr(Config, "orphaned_at")
    assert not hasattr(Config, "origin_solution_slug")
    assert not hasattr(Config, "origin_solution_id")


@pytest.mark.e2e
async def test_config_value_resolves_by_standard_org_scope(db_session) -> None:
    db = db_session
    org = await _make_org(db)

    await _set_value(db, org.id, "REGION", "us-west")

    repo = ConfigRepository(db, org_id=org.id, is_superuser=True)
    assert (await repo.merged_for_sdk())["REGION"]["value"] == "us-west"
    assert (await repo.get_config_strict("REGION")).value["value"] == "us-west"
    assert (await repo.get_config("REGION")).value["value"] == "us-west"


@pytest.mark.e2e
async def test_reset_in_scope_updates_existing_config_value(db_session) -> None:
    db = db_session
    org = await _make_org(db)

    await _set_value(db, org.id, "REGION", "us-west")
    await _set_value(db, org.id, "REGION", "eu-central")

    repo = ConfigRepository(db, org_id=org.id, is_superuser=True)
    assert (await repo.merged_for_sdk())["REGION"]["value"] == "eu-central"
