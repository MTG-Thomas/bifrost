"""Integration config schema validation and response building."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, get_args

from pydantic import ValidationError

from src.models.contracts.integrations import ConfigItemType, ConfigSchemaItem, IntegrationResponse

if TYPE_CHECKING:
    from src.models.orm.integrations import Integration, IntegrationConfigSchema

logger = logging.getLogger(__name__)

VALID_CONFIG_SCHEMA_TYPES: frozenset[str] = frozenset(get_args(ConfigItemType))


def validate_config_schema_type(type_value: str) -> ConfigItemType:
    """Validate a config schema type before persisting to the database."""
    if type_value not in VALID_CONFIG_SCHEMA_TYPES:
        allowed = ", ".join(sorted(VALID_CONFIG_SCHEMA_TYPES))
        raise ValueError(
            f"Invalid config schema type {type_value!r}; must be one of: {allowed}"
        )
    return type_value  # type: ignore[return-value]


def parse_config_schema_items(
    orm_items: list[IntegrationConfigSchema] | None,
    *,
    integration_name: str | None = None,
    log_warnings: bool = True,
) -> tuple[list[ConfigSchemaItem], list[str]]:
    """Parse ORM config schema rows, skipping invalid items with warnings."""
    if not orm_items:
        return [], []

    valid_items: list[ConfigSchemaItem] = []
    warnings: list[str] = []

    for item in orm_items:
        try:
            valid_items.append(ConfigSchemaItem.model_validate(item))
        except ValidationError as exc:
            detail = exc.errors()[0]["msg"] if exc.errors() else "validation failed"
            msg = f"config_schema.{item.key}: {detail}"
            warnings.append(msg)
            if log_warnings:
                log_ctx = (
                    f"integration {integration_name!r}"
                    if integration_name
                    else "integration"
                )
                logger.warning(
                    "Skipping invalid config schema item for %s: %s",
                    log_ctx,
                    msg,
                )

    return valid_items, warnings


def integration_to_response(integration: Integration) -> IntegrationResponse:
    """Build IntegrationResponse from ORM, tolerating invalid config_schema items."""
    config_schema, warnings = parse_config_schema_items(
        integration.config_schema,
        integration_name=integration.name,
    )

    if integration.config_schema:
        config_schema_out: list[ConfigSchemaItem] | None = config_schema
    else:
        config_schema_out = None

    return IntegrationResponse(
        id=integration.id,
        name=integration.name,
        list_entities_data_provider_id=integration.list_entities_data_provider_id,
        config_schema=config_schema_out,
        entity_id=integration.entity_id,
        entity_id_name=integration.entity_id_name,
        default_entity_id=integration.default_entity_id,
        has_oauth_config=integration.has_oauth_config,
        is_deleted=integration.is_deleted,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
        validation_warning="; ".join(warnings) if warnings else None,
    )
