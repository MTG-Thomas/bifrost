from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.enums import ConfigType
from src.repositories.integrations import (
    IntegrationMappingRepository,
    IntegrationsRepository,
)


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values

    def first(self):
        return self._values[0] if self._values else None


class _ExecuteResult:
    def __init__(self, *, scalars=None, scalar_one_or_none=None):
        self._scalars = scalars or []
        self._scalar_one_or_none = scalar_one_or_none

    def scalars(self):
        return _ScalarsResult(self._scalars)

    def scalar_one_or_none(self):
        return self._scalar_one_or_none


@pytest.fixture
def session():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _config_entry(key, value, *, organization_id=None, config_type=ConfigType.STRING):
    return SimpleNamespace(
        key=key,
        value=value,
        organization_id=organization_id,
        config_type=config_type,
    )


async def test_save_config_deletes_blank_updates_existing_and_inserts_missing(session):
    repository = IntegrationsRepository(session)
    integration_id = uuid4()
    org_id = uuid4()
    timeout_schema_id = uuid4()

    session.execute.side_effect = [
        _ExecuteResult(
            scalars=[
                SimpleNamespace(key="timeout", id=timeout_schema_id),
                SimpleNamespace(key="api_key", id=uuid4()),
            ]
        ),
        _ExecuteResult(),  # delete blank base_url override
        _ExecuteResult(scalar_one_or_none=uuid4()),  # existing timeout config
        _ExecuteResult(),  # update timeout
        _ExecuteResult(scalar_one_or_none=None),  # missing api_key config
    ]

    await repository._save_config(
        integration_id,
        org_id,
        {"base_url": "", "timeout": 30, "api_key": "secret"},
        updated_by="tester",
    )

    assert session.execute.await_count == 5
    session.add.assert_called_once()
    inserted = session.add.call_args.args[0]
    assert inserted.integration_id == integration_id
    assert inserted.organization_id == org_id
    assert inserted.key == "api_key"
    assert inserted.value == {"value": "secret"}
    assert inserted.updated_by == "tester"


async def test_save_config_uses_null_org_scope_for_integration_defaults(session):
    repository = IntegrationsRepository(session)
    integration_id = uuid4()

    session.execute.side_effect = [
        _ExecuteResult(scalars=[]),
        _ExecuteResult(scalar_one_or_none=None),
    ]

    await repository._save_config(
        integration_id,
        None,
        {"base_url": "https://default.example"},
        updated_by="admin",
    )

    session.add.assert_called_once()
    inserted = session.add.call_args.args[0]
    assert inserted.integration_id == integration_id
    assert inserted.organization_id is None
    assert inserted.key == "base_url"
    assert inserted.value == {"value": "https://default.example"}


async def test_get_config_for_mapping_external_reads_only_org_tier_query(session):
    repository = IntegrationsRepository(session)
    integration_id = uuid4()
    org_id = uuid4()
    session.execute.return_value = _ExecuteResult(
        scalars=[
            _config_entry(
                "base_url",
                {"value": "https://org.example"},
                organization_id=org_id,
            )
        ]
    )

    result = await repository.get_config_for_mapping(
        integration_id,
        org_id,
        external=True,
    )

    assert result == {"base_url": "https://org.example"}
    statement = session.execute.call_args.args[0]
    compiled_params = statement.compile().params.values()
    assert integration_id in compiled_params
    assert org_id in compiled_params


async def test_get_config_for_mapping_keeps_running_when_secret_decrypt_fails(session):
    repository = IntegrationsRepository(session)
    integration_id = uuid4()
    org_id = uuid4()
    session.execute.return_value = _ExecuteResult(
        scalars=[
            _config_entry(
                "api_key",
                {"value": "bad-ciphertext"},
                organization_id=org_id,
                config_type=ConfigType.SECRET,
            ),
            _config_entry(
                "base_url",
                {"value": "https://org.example"},
                organization_id=org_id,
            ),
        ]
    )

    with patch("src.repositories.integrations.decrypt_secret", side_effect=ValueError):
        result = await repository.get_config_for_mapping(integration_id, org_id)

    assert result == {"api_key": None, "base_url": "https://org.example"}


async def test_get_integration_defaults_never_exposes_global_tier_to_external_callers(
    session,
):
    repository = IntegrationsRepository(session)

    result = await repository.get_integration_defaults(uuid4(), external=True)

    assert result == {}
    session.execute.assert_not_awaited()


async def test_get_integration_defaults_decrypts_secret_defaults(session):
    repository = IntegrationsRepository(session)
    integration_id = uuid4()
    session.execute.return_value = _ExecuteResult(
        scalars=[
            _config_entry("api_key", "encrypted", config_type=ConfigType.SECRET),
            _config_entry("base_url", {"value": "https://default.example"}),
        ]
    )

    with patch("src.repositories.integrations.decrypt_secret", return_value="plain"):
        result = await repository.get_integration_defaults(integration_id, external=False)

    assert result == {
        "api_key": "plain",
        "base_url": "https://default.example",
    }


async def test_list_mappings_applies_org_filter_when_supplied(session):
    repository = IntegrationsRepository(session)
    integration_id = uuid4()
    org_id = uuid4()
    mapping = SimpleNamespace(
        id=uuid4(),
        integration_id=integration_id,
        organization_id=org_id,
    )
    result = MagicMock()
    result.unique.return_value = result
    result.scalars.return_value.all.return_value = [mapping]
    session.execute.return_value = result

    mappings = await repository.list_mappings(integration_id, organization_id=org_id)

    assert mappings == [mapping]
    statement = session.execute.call_args.args[0]
    compiled_params = statement.compile().params.values()
    assert integration_id in compiled_params
    assert org_id in compiled_params


async def test_mapping_repository_lookup_returns_none_when_integration_missing(session):
    repository = IntegrationMappingRepository(session, org_id=uuid4())
    session.execute.return_value = _ExecuteResult(scalar_one_or_none=None)
    repository.get = AsyncMock()

    result = await repository.get_by_integration_name("missing")

    assert result is None
    repository.get.assert_not_awaited()


async def test_mapping_repository_lookup_uses_cascade_get_for_found_integration(session):
    repository = IntegrationMappingRepository(session, org_id=uuid4())
    integration = SimpleNamespace(id=uuid4(), name="Halo")
    mapping = SimpleNamespace(id=uuid4(), integration_id=integration.id)
    session.execute.return_value = _ExecuteResult(scalar_one_or_none=integration)
    repository.get = AsyncMock(return_value=mapping)

    result = await repository.get_by_integration_name("Halo")

    assert result == mapping
    repository.get.assert_awaited_once_with(integration_id=integration.id)
