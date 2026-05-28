"""Unit tests for integration config schema validation and response building."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from bifrost.manifest import ManifestIntegrationConfigSchema
from src.services.integration_config_schema import (
    integration_to_response,
    parse_config_schema_items,
    validate_config_schema_type,
)


def _schema_row(*, key: str, type_: str, required: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        key=key,
        type=type_,
        required=required,
        description=None,
        options=None,
    )


def _integration(*, name: str, config_schema: list[SimpleNamespace] | None) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        name=name,
        list_entities_data_provider_id=None,
        config_schema=config_schema,
        entity_id=None,
        entity_id_name=None,
        default_entity_id=None,
        has_oauth_config=False,
        is_deleted=False,
        created_at=now,
        updated_at=now,
    )


class TestValidateConfigSchemaType:
    def test_accepts_valid_types(self):
        for type_value in ("string", "int", "bool", "json", "secret"):
            assert validate_config_schema_type(type_value) == type_value

    def test_rejects_invalid_type(self):
        with pytest.raises(ValueError, match="Invalid config schema type 'dropdown'"):
            validate_config_schema_type("dropdown")


class TestParseConfigSchemaItems:
    def test_returns_empty_for_none(self):
        items, warnings = parse_config_schema_items(None, log_warnings=False)
        assert items == []
        assert warnings == []

    def test_parses_valid_items(self):
        items, warnings = parse_config_schema_items(
            [_schema_row(key="api_url", type_="string")],
            log_warnings=False,
        )
        assert len(items) == 1
        assert items[0].key == "api_url"
        assert warnings == []

    def test_skips_invalid_items_with_warning(self, caplog):
        rows = [
            _schema_row(key="good", type_="string"),
            _schema_row(key="bad", type_="dropdown"),
        ]
        items, warnings = parse_config_schema_items(
            rows,
            integration_name="Autotask",
            log_warnings=True,
        )

        assert len(items) == 1
        assert items[0].key == "good"
        assert warnings[0].startswith("config_schema.bad:")
        assert "dropdown" in warnings[0]
        assert any("Skipping invalid config schema item" in r.message for r in caplog.records)


class TestIntegrationToResponse:
    def test_valid_integration_has_no_warning(self):
        integration = _integration(
            name="Good",
            config_schema=[_schema_row(key="api_url", type_="string")],
        )
        response = integration_to_response(integration)

        assert response.name == "Good"
        assert response.config_schema is not None
        assert len(response.config_schema) == 1
        assert response.validation_warning is None

    def test_invalid_schema_item_omitted_with_warning(self):
        integration = _integration(
            name="Autotask",
            config_schema=[
                _schema_row(key="valid", type_="secret"),
                _schema_row(key="broken", type_="dropdown"),
            ],
        )
        response = integration_to_response(integration)

        assert [item.key for item in response.config_schema or []] == ["valid"]
        assert response.validation_warning is not None
        assert response.validation_warning.startswith("config_schema.broken:")
        assert "Input should" in response.validation_warning

    def test_list_resilience_mixed_integrations(self):
        good = integration_to_response(
            _integration(name="Good", config_schema=[_schema_row(key="x", type_="string")])
        )
        bad = integration_to_response(
            _integration(
                name="Autotask",
                config_schema=[_schema_row(key="broken", type_="dropdown")],
            )
        )

        assert good.validation_warning is None
        assert bad.validation_warning is not None
        assert bad.config_schema == []


class TestManifestIntegrationConfigSchemaGuardrail:
    def test_rejects_invalid_type_on_import(self):
        with pytest.raises(ValidationError) as exc_info:
            ManifestIntegrationConfigSchema(key="broken", type="dropdown")  # type: ignore[arg-type]

        assert any(e["loc"] == ("type",) for e in exc_info.value.errors())

    def test_accepts_valid_type_on_import(self):
        item = ManifestIntegrationConfigSchema(key="api_key", type="secret")
        assert item.type == "secret"
