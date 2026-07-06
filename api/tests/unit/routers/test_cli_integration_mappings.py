from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.cli import (
    SDKIntegrationsDeleteMappingRequest,
    SDKIntegrationsGetMappingRequest,
    SDKIntegrationsListMappingsRequest,
    SDKIntegrationsUpsertMappingRequest,
)
from src.routers import cli


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


def _principal(*, org_id: UUID | None = None, is_external: bool = False) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="user@example.test",
        organization_id=org_id or uuid4(),
        is_superuser=False,
        is_external=is_external,
    )


def _db(*, provider_org: bool = False):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=ScalarResult(provider_org))
    db.commit = AsyncMock()
    return db


def _integration(*, integration_id: UUID | None = None):
    return SimpleNamespace(id=integration_id or uuid4())


def _mapping(
    *,
    integration_id: UUID | None = None,
    organization_id: UUID | None = None,
    entity_id: str = "tenant-1",
    entity_name: str | None = "Tenant 1",
):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        integration_id=integration_id or uuid4(),
        organization_id=organization_id or uuid4(),
        entity_id=entity_id,
        entity_name=entity_name,
        oauth_token_id=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_sdk_find_integration_mapping_prefers_resolved_org_mapping() -> None:
    integration_id = uuid4()
    org_id = uuid4()
    mapping = _mapping(integration_id=integration_id, organization_id=org_id)
    repo = MagicMock()
    repo.get_mapping_by_org = AsyncMock(return_value=mapping)
    repo.list_mappings = AsyncMock()

    result = await cli._sdk_find_integration_mapping(
        repo,
        integration_id,
        str(org_id),
        SDKIntegrationsGetMappingRequest(name="Autotask", entity_id="other"),
    )

    assert result is mapping
    repo.get_mapping_by_org.assert_awaited_once_with(integration_id, org_id)
    repo.list_mappings.assert_not_awaited()


@pytest.mark.asyncio
async def test_sdk_find_integration_mapping_searches_entity_when_scope_allows() -> None:
    integration_id = uuid4()
    target = _mapping(integration_id=integration_id, entity_id="target")
    repo = MagicMock()
    repo.get_mapping_by_org = AsyncMock(return_value=None)
    repo.list_mappings = AsyncMock(return_value=[
        _mapping(integration_id=integration_id, entity_id="other"),
        target,
    ])

    result = await cli._sdk_find_integration_mapping(
        repo,
        integration_id,
        None,
        SDKIntegrationsGetMappingRequest(name="Autotask", scope="global", entity_id="target"),
    )

    assert result is target
    repo.list_mappings.assert_awaited_once_with(integration_id, organization_id=None)


@pytest.mark.asyncio
async def test_sdk_integrations_list_mappings_scopes_provider_org_to_all_mappings() -> None:
    org_id = uuid4()
    integration = _integration()
    mapping = _mapping(integration_id=integration.id, organization_id=uuid4())
    repo = MagicMock()
    repo.get_integration_by_name = AsyncMock(return_value=integration)
    repo.list_mappings = AsyncMock(return_value=[mapping])
    repo.get_config_for_mapping = AsyncMock(return_value={"base_url": "https://example.test"})

    with (
        patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=str(org_id))),
        patch.object(cli, "_is_external_user_db", AsyncMock(return_value=False)),
        patch("src.repositories.integrations.IntegrationsRepository", return_value=repo),
    ):
        result = await cli.sdk_integrations_list_mappings(
            SDKIntegrationsListMappingsRequest(name="Autotask"),
            _principal(org_id=org_id),
            _db(provider_org=True),
        )

    assert result is not None
    assert result.items[0].config == {"base_url": "https://example.test"}
    repo.list_mappings.assert_awaited_once_with(integration.id)
    repo.get_config_for_mapping.assert_awaited_once_with(
        integration.id,
        mapping.organization_id,
        include_default_secrets=True,
        external=False,
    )


@pytest.mark.asyncio
async def test_sdk_integrations_get_mapping_returns_item_config() -> None:
    org_id = uuid4()
    integration = _integration()
    mapping = _mapping(integration_id=integration.id, organization_id=org_id)
    repo = MagicMock()
    repo.get_integration_by_name = AsyncMock(return_value=integration)
    repo.get_mapping_by_org = AsyncMock(return_value=mapping)
    repo.get_config_for_mapping = AsyncMock(return_value={"api_key": "masked"})

    with (
        patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=str(org_id))),
        patch.object(cli, "_is_external_user_db", AsyncMock(return_value=True)),
        patch("src.repositories.integrations.IntegrationsRepository", return_value=repo),
    ):
        result = await cli.sdk_integrations_get_mapping(
            SDKIntegrationsGetMappingRequest(name="Autotask", scope=str(org_id)),
            _principal(org_id=org_id, is_external=True),
            _db(),
        )

    assert result is not None
    assert result.organization_id == str(org_id)
    assert result.config == {"api_key": "masked"}
    repo.get_config_for_mapping.assert_awaited_once_with(
        integration.id,
        org_id,
        include_default_secrets=False,
        external=True,
    )


@pytest.mark.asyncio
async def test_sdk_integrations_upsert_mapping_updates_existing_mapping() -> None:
    org_id = uuid4()
    integration = _integration()
    existing = _mapping(integration_id=integration.id, organization_id=org_id)
    updated = _mapping(integration_id=integration.id, organization_id=org_id, entity_name="Updated")
    repo = MagicMock()
    repo.get_integration_by_name = AsyncMock(return_value=integration)
    repo.get_mapping_by_org = AsyncMock(return_value=existing)
    repo.update_mapping = AsyncMock(return_value=updated)
    repo.create_mapping = AsyncMock()
    repo.get_config_for_mapping = AsyncMock(return_value={"region": "us"})
    db = _db()

    with (
        patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=str(org_id))),
        patch.object(cli, "_is_external_user_db", AsyncMock(return_value=False)),
        patch("src.repositories.integrations.IntegrationsRepository", return_value=repo),
    ):
        result = await cli.sdk_integrations_upsert_mapping(
            SDKIntegrationsUpsertMappingRequest(
                name="Autotask",
                scope=str(org_id),
                entity_id="tenant-2",
                entity_name="Updated",
                config={"region": "us"},
            ),
            _principal(org_id=org_id),
            db,
        )

    assert result.entity_name == "Updated"
    db.commit.assert_awaited_once()
    repo.update_mapping.assert_awaited_once()
    repo.create_mapping.assert_not_awaited()


@pytest.mark.asyncio
async def test_sdk_integrations_delete_mapping_reports_deleted_and_missing() -> None:
    org_id = uuid4()
    integration = _integration()
    mapping = _mapping(integration_id=integration.id, organization_id=org_id)
    repo = MagicMock()
    repo.get_integration_by_name = AsyncMock(return_value=integration)
    repo.get_mapping_by_org = AsyncMock(side_effect=[mapping, None])
    repo.delete_mapping = AsyncMock(return_value=True)
    db = _db()

    with (
        patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=str(org_id))),
        patch("src.repositories.integrations.IntegrationsRepository", return_value=repo),
    ):
        deleted = await cli.sdk_integrations_delete_mapping(
            SDKIntegrationsDeleteMappingRequest(name="Autotask", scope=str(org_id)),
            _principal(org_id=org_id),
            db,
        )
        missing = await cli.sdk_integrations_delete_mapping(
            SDKIntegrationsDeleteMappingRequest(name="Autotask", scope=str(org_id)),
            _principal(org_id=org_id),
            db,
        )

    assert deleted == {"deleted": True}
    assert missing == {"deleted": False}
    repo.delete_mapping.assert_awaited_once_with(mapping.id)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_sdk_integrations_upsert_mapping_rejects_global_scope() -> None:
    integration = _integration()
    repo = MagicMock()
    repo.get_integration_by_name = AsyncMock(return_value=integration)

    with (
        patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=None)),
        patch("src.repositories.integrations.IntegrationsRepository", return_value=repo),
    ):
        with pytest.raises(HTTPException) as exc:
            await cli.sdk_integrations_upsert_mapping(
                SDKIntegrationsUpsertMappingRequest(
                    name="Autotask",
                    scope="global",
                    entity_id="tenant-2",
                    entity_name=None,
                    config=None,
                ),
                _principal(),
                _db(),
            )

    assert exc.value.status_code == 400
