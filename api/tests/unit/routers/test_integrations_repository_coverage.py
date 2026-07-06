from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.enums import ConfigType
from src.routers.integrations import IntegrationsRepository


def _session() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def _result(rows=(), scalar=None):
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(rows)
    result.scalar_one_or_none.return_value = scalar
    return result


class TestIntegrationConfigValidation:
    @pytest.mark.asyncio
    async def test_validate_config_value_accepts_empty_and_valid_values(self):
        repo = IntegrationsRepository(_session())

        await repo._validate_config_value("count", "", "int")
        await repo._validate_config_value("count", None, "int")
        await repo._validate_config_value("count", 3, "int")
        await repo._validate_config_value("count", "3", "int")
        await repo._validate_config_value("enabled", True, "bool")
        await repo._validate_config_value("payload", '{"ok": true}', "json")
        await repo._validate_config_value("payload", {"ok": True}, "json")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("key", "value", "schema_type", "message"),
        [
            ("count", "abc", "int", "expects integer"),
            ("count", 1.5, "int", "expects integer"),
            ("enabled", "true", "bool", "expects boolean"),
            ("payload", "{bad", "json", "invalid JSON"),
        ],
    )
    async def test_validate_config_value_rejects_bad_values(
        self,
        key,
        value,
        schema_type,
        message,
    ):
        repo = IntegrationsRepository(_session())

        with pytest.raises(HTTPException) as exc:
            await repo._validate_config_value(key, value, schema_type)

        assert exc.value.status_code == 400
        assert message in exc.value.detail


class TestIntegrationConfigPersistence:
    @pytest.mark.asyncio
    async def test_save_config_deletes_empty_values_and_inserts_typed_values(self):
        db = _session()
        integration_id = uuid4()
        organization_id = uuid4()
        schema_rows = [
            SimpleNamespace(id=uuid4(), key="token", type="secret"),
            SimpleNamespace(id=uuid4(), key="retries", type="int"),
            SimpleNamespace(id=uuid4(), key="enabled", type="bool"),
            SimpleNamespace(id=uuid4(), key="payload", type="json"),
        ]
        db.execute = AsyncMock(
            side_effect=[
                _result(schema_rows),
                MagicMock(),
                _result(scalar=None),
                _result(scalar=None),
                _result(scalar=None),
                _result(scalar=None),
            ]
        )
        repo = IntegrationsRepository(db)

        with patch(
            "src.core.security.encrypt_secret",
            return_value="encrypted-secret",
        ) as encrypt_secret:
            await repo._save_config(
                integration_id=integration_id,
                organization_id=organization_id,
                config={
                    "clear_me": "",
                    "token": "plain-secret",
                    "retries": "5",
                    "enabled": True,
                    "payload": {"mode": "sync"},
                },
                updated_by="tester",
            )

        encrypt_secret.assert_called_once_with("plain-secret")
        assert db.execute.await_count == 6
        assert db.add.call_count == 4
        added_by_key = {call.args[0].key: call.args[0] for call in db.add.call_args_list}
        assert added_by_key["token"].value == {"value": "encrypted-secret"}
        assert added_by_key["token"].config_type == ConfigType.SECRET
        assert added_by_key["retries"].config_type == ConfigType.INT
        assert added_by_key["enabled"].config_type == ConfigType.BOOL
        assert added_by_key["payload"].config_type == ConfigType.JSON
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_config_updates_existing_rows(self):
        db = _session()
        schema_id = uuid4()
        existing_config_id = uuid4()
        db.execute = AsyncMock(
            side_effect=[
                _result([SimpleNamespace(id=schema_id, key="region", type="string")]),
                _result(scalar=existing_config_id),
                MagicMock(),
            ]
        )
        repo = IntegrationsRepository(db)

        await repo._save_config(
            integration_id=uuid4(),
            organization_id=None,
            config={"region": "us-east"},
            updated_by="tester",
        )

        assert db.add.call_count == 0
        assert db.execute.await_count == 3
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_extract_config_value_decrypts_secret_or_returns_legacy_plaintext(self):
        repo = IntegrationsRepository(_session())

        with patch("src.core.security.decrypt_secret", return_value="plain"):
            decrypted = await repo._extract_config_value(
                SimpleNamespace(
                    value={"value": "cipher"},
                    config_type=ConfigType.SECRET,
                )
            )
        assert decrypted == "plain"

        with patch("src.core.security.decrypt_secret", side_effect=ValueError("bad")):
            legacy = await repo._extract_config_value(
                SimpleNamespace(
                    value={"value": "legacy"},
                    config_type=ConfigType.SECRET,
                )
            )
        assert legacy == "legacy"

        raw = await repo._extract_config_value(
            SimpleNamespace(value={"value": 5}, config_type=ConfigType.INT)
        )
        assert raw == 5

    @pytest.mark.asyncio
    async def test_config_read_helpers_merge_defaults_and_overrides(self):
        repo = IntegrationsRepository(_session())
        integration_id = uuid4()
        org_id = uuid4()
        repo.get_integration_defaults = AsyncMock(return_value={"a": 1, "shared": "base"})
        repo.get_org_config_overrides = AsyncMock(
            return_value={"shared": "org", "b": 2}
        )

        result = await repo.get_config_for_mapping(
            integration_id,
            org_id,
            external=False,
        )

        assert result == {"a": 1, "shared": "org", "b": 2}
        repo.get_integration_defaults.assert_awaited_once_with(
            integration_id,
            external=False,
        )
        repo.get_org_config_overrides.assert_awaited_once_with(integration_id, org_id)

    @pytest.mark.asyncio
    async def test_external_defaults_return_empty_without_querying(self):
        db = _session()
        repo = IntegrationsRepository(db)

        result = await repo.get_integration_defaults(uuid4(), external=True)

        assert result == {}
        db.execute.assert_not_awaited()
