"""Config repository."""

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select

from src.core.log_safety import log_safe
from src.core.org_filter import OrgFilterType
from src.models import (
    Config as ConfigModel,
    ConfigResponse,
    ConfigType,
    SetConfigRequest,
    UpdateConfigRequest,
)
from src.models.enums import ConfigType as ConfigTypeEnum
from src.models.orm.integrations import Integration
from src.repositories.org_scoped import OrgScopedRepository

logger = logging.getLogger(__name__)
_VALUE_NOT_PROVIDED = object()


class ConfigRepository(OrgScopedRepository[ConfigModel]):  # type: ignore[type-var]
    """Repository for configuration values."""

    model = ConfigModel
    role_table = None

    async def list_configs(
        self,
        filter_type: OrgFilterType = OrgFilterType.ORG_PLUS_GLOBAL,
    ) -> list[ConfigResponse]:
        """List configs with specified filter type."""
        query = self._list_configs_query(filter_type).order_by(self.model.key)
        result = await self.session.execute(query)
        return [
            self._config_response(config, integration_name=integration_name)
            for config, integration_name in result.all()
        ]

    def _list_configs_query(self, filter_type: OrgFilterType):
        query = select(self.model, Integration.name.label("integration_name")).outerjoin(
            Integration,
            and_(
                self.model.integration_id == Integration.id,
                Integration.is_deleted.is_(False),
            ),
        )

        if filter_type == OrgFilterType.ALL:
            pass
        elif filter_type == OrgFilterType.GLOBAL_ONLY:
            query = query.where(self.model.organization_id.is_(None))
        elif filter_type == OrgFilterType.ORG_ONLY:
            query = query.where(self.model.organization_id == self.org_id)
        else:
            query = self._apply_org_plus_global_filter(query)
        return query

    def _apply_org_plus_global_filter(self, query):
        if self.org_id is None:
            return query.where(self.model.organization_id.is_(None))
        return query.where(
            or_(
                self.model.organization_id == self.org_id,
                self.model.organization_id.is_(None),
            )
        )

    @staticmethod
    def _raw_config_value(config: ConfigModel) -> Any:
        return config.value.get("value") if isinstance(config.value, dict) else config.value

    @classmethod
    def _config_response(
        cls,
        config: ConfigModel,
        *,
        integration_name: str | None = None,
        value: Any = _VALUE_NOT_PROVIDED,
        response_type: ConfigType | None = None,
    ) -> ConfigResponse:
        value_provided = value is not _VALUE_NOT_PROVIDED
        raw_value = value if value_provided else cls._raw_config_value(config)
        display_value = (
            "[SECRET]"
            if config.config_type == ConfigTypeEnum.SECRET and not value_provided
            else raw_value
        )
        resolved_type = response_type or (
            ConfigType(config.config_type.value)
            if config.config_type
            else ConfigType.STRING
        )
        return ConfigResponse(
            id=config.id,
            key=config.key,
            value=display_value,
            type=resolved_type,
            scope="org" if config.organization_id else "GLOBAL",
            org_id=str(config.organization_id) if config.organization_id else None,
            integration_id=str(config.integration_id) if config.integration_id else None,
            integration_name=integration_name,
            description=config.description,
            updated_at=config.updated_at,
            updated_by=config.updated_by,
        )

    async def get_config(self, key: str) -> ConfigModel | None:
        """Get config by key with cascade scoping: org-specific > global."""
        return await self.get(key=key)

    @staticmethod
    def _config_entry(config: ConfigModel) -> dict[str, Any]:
        value = (
            config.value.get("value")
            if isinstance(config.value, dict)
            else config.value
        )
        return {
            "value": value,
            "type": config.config_type.value if config.config_type else "string",
        }

    async def _read_sdk_config_cache(self, org_id_str: str | None) -> dict[str, Any] | None:
        from src.core.cache.keys import config_hash_key_versioned
        from src.core.cache.redis_client import get_shared_redis

        try:
            redis = await get_shared_redis()
            hash_key = await config_hash_key_versioned(redis, org_id_str)
            cached = await redis.hgetall(hash_key)  # type: ignore[misc]
        except Exception as e:
            logger.warning(f"Config cache read failed: {e}")
            return None
        if not cached:
            return None

        out: dict[str, Any] = {}
        for key, value in cached.items():
            key_str = key.decode() if isinstance(key, bytes) else key
            value_str = value.decode() if isinstance(value, bytes) else value
            try:
                out[key_str] = json.loads(value_str)
            except json.JSONDecodeError:
                out[key_str] = {"value": value_str, "type": "string"}
        return out

    async def _write_sdk_config_cache(
        self, org_id_str: str | None, config_dict: dict[str, Any]
    ) -> None:
        from src.core.cache.keys import TTL_CONFIG, config_hash_key_versioned
        from src.core.cache.redis_client import get_shared_redis

        if not config_dict:
            return
        try:
            redis = await get_shared_redis()
            hash_key = await config_hash_key_versioned(redis, org_id_str)
            mapping = {key: json.dumps(value) for key, value in config_dict.items()}
            await redis.hset(hash_key, mapping=mapping)  # type: ignore[misc]
            await redis.expire(hash_key, TTL_CONFIG)
        except Exception as e:
            logger.warning(f"Config cache write failed: {e}")

    async def merged_for_sdk(self) -> dict[str, Any]:
        """Return the full merged config dict for this scope."""
        org_id_str = str(self.org_id) if self.org_id is not None else None

        cached = await self._read_sdk_config_cache(org_id_str)
        if cached is not None:
            return cached

        config_dict: dict[str, Any] = {}

        global_q = select(self.model).where(
            self.model.organization_id.is_(None),
            self.model.integration_id.is_(None),
        )
        global_rows = (await self.session.execute(global_q)).scalars()
        for config in global_rows:
            config_dict[config.key] = self._config_entry(config)

        if self.org_id is not None:
            org_q = select(self.model).where(
                self.model.organization_id == self.org_id,
                self.model.integration_id.is_(None),
            )
            org_rows = (await self.session.execute(org_q)).scalars()
            for config in org_rows:
                config_dict[config.key] = self._config_entry(config)

        await self._write_sdk_config_cache(org_id_str, config_dict)

        logger.debug(
            f"Loaded {len(config_dict)} config entries for org={log_safe(org_id_str)}"
        )
        return config_dict

    async def get_config_strict(self, key: str) -> ConfigModel | None:
        """Get config strictly in current org scope."""
        query = select(self.model).where(
            self.model.key == key,
            self.model.organization_id == self.org_id,
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def set_config(
        self, request: SetConfigRequest, updated_by: str
    ) -> ConfigResponse:
        """Create or update a config in current org scope."""
        now = datetime.now(timezone.utc)

        stored_value = request.value
        if request.type == ConfigType.SECRET:
            from src.core.security import encrypt_secret

            stored_value = encrypt_secret(request.value)

        db_config_type = (
            ConfigTypeEnum(request.type.value)
            if request.type
            else ConfigTypeEnum.STRING
        )

        existing = await self.get_config_strict(request.key)

        if existing:
            existing.value = {"value": stored_value}
            existing.config_type = db_config_type
            existing.description = request.description
            existing.updated_at = now
            existing.updated_by = updated_by
            await self.session.flush()
            await self.session.refresh(existing)
            config = existing
        else:
            config = ConfigModel(
                key=request.key,
                value={"value": stored_value},
                config_type=db_config_type,
                description=request.description,
                organization_id=self.org_id,
                created_at=now,
                updated_at=now,
                updated_by=updated_by,
            )
            self.session.add(config)
            await self.session.flush()
            await self.session.refresh(config)

        logger.info(f"Set config {log_safe(request.key)} in org {self.org_id}")

        return self._config_response(
            config,
            value=self._raw_config_value(config),
            response_type=request.type if request.type else ConfigType.STRING,
        )

    async def update_config_by_id(
        self,
        config_id: UUID,
        request: UpdateConfigRequest,
        updated_by: str,
    ) -> tuple[ConfigResponse, UUID | None, str] | None:
        """Update a config by ID and return the prior cache identity."""
        query = select(self.model).where(self.model.id == config_id)
        result = await self.session.execute(query)
        config = result.scalar_one_or_none()
        if not config:
            return None

        old_org_id = config.organization_id
        old_key = config.key
        now = datetime.now(timezone.utc)

        effective_type = self._effective_config_type(config, request)
        self._apply_config_value_update(config, request, effective_type)
        self._apply_config_metadata_update(config, request)
        config.updated_at = now
        config.updated_by = updated_by
        await self.session.flush()
        await self.session.refresh(config)

        logger.info(
            f"Updated config {log_safe(config.key)} "
            f"(id={log_safe(config_id)}) org={log_safe(config.organization_id)}"
        )

        response = self._config_response(
            config,
            value=self._raw_config_value(config),
            response_type=self._effective_config_type(config, request),
        )
        return response, old_org_id, old_key

    @staticmethod
    def _effective_config_type(
        config: ConfigModel, request: UpdateConfigRequest
    ) -> ConfigType:
        if request.type is not None:
            return request.type
        if config.config_type:
            return ConfigType(config.config_type.value)
        return ConfigType.STRING

    @staticmethod
    def _apply_config_value_update(
        config: ConfigModel,
        request: UpdateConfigRequest,
        effective_type: ConfigType,
    ) -> None:
        if request.value is None:
            return
        if request.value == "" and effective_type == ConfigType.SECRET:
            return
        stored_value = request.value
        if effective_type == ConfigType.SECRET and request.value != "":
            from src.core.security import encrypt_secret

            stored_value = encrypt_secret(request.value)
        config.value = {"value": stored_value}

    @staticmethod
    def _apply_config_metadata_update(
        config: ConfigModel, request: UpdateConfigRequest
    ) -> None:
        if request.key is not None:
            config.key = request.key
        if request.type is not None:
            config.config_type = ConfigTypeEnum(request.type.value)
        fields_set = request.model_fields_set or set()
        if "description" in fields_set:
            config.description = request.description
        if "organization_id" in fields_set:
            config.organization_id = request.organization_id

    async def delete_config(self, config_id: UUID) -> ConfigModel | None:
        """Delete config by ID. Returns the deleted config or None if missing."""
        query = select(self.model).where(self.model.id == config_id)
        result = await self.session.execute(query)
        config = result.scalar_one_or_none()
        if not config:
            return None

        key = config.key
        await self.session.delete(config)
        await self.session.flush()

        logger.info(f"Deleted config {log_safe(key)} (id={log_safe(config_id)})")
        return config
