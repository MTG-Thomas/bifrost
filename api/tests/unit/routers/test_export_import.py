"""Tests for export/import models and serialization."""

import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.models.contracts.export_import import (
    BulkExportRequest,
    ConfigExportFile,
    ConfigExportItem,
    ConfigSchemaExportItem,
    ImportResult,
    ImportResultItem,
    IntegrationExportFile,
    IntegrationMappingExportItem,
    KnowledgeExportFile,
    KnowledgeExportItem,
    OAuthProviderExportItem,
    TableExportFile,
    TableExportItem,
)
from src.models.enums import ConfigType
from src.routers import export_import
from src.routers.export_import import (
    _import_config_value,
    _import_oauth_provider,
    _json_response,
    _parse_target_org,
    _resolve_org_id,
    _sync_config_schema,
    import_configs,
    import_knowledge,
)


class TestKnowledgeExport:
    def test_knowledge_export_serialization(self):
        """KnowledgeExportFile serializes correctly."""
        export = KnowledgeExportFile(
            item_count=1,
            items=[{
                "namespace": "docs",
                "key": "intro",
                "content": "Hello world",
                "metadata": {"source": "manual"},
                "organization_id": None,
            }],
        )
        data = json.loads(export.model_dump_json())
        assert data["bifrost_export_version"] == "1.0"
        assert data["entity_type"] == "knowledge"
        assert data["contains_encrypted_values"] is False
        assert len(data["items"]) == 1
        assert data["items"][0]["namespace"] == "docs"
        assert data["items"][0]["content"] == "Hello world"

    def test_knowledge_export_roundtrip(self):
        """Export JSON can be parsed back into model."""
        export = KnowledgeExportFile(
            item_count=2,
            items=[
                {"namespace": "docs", "key": "a", "content": "content a", "metadata": {}},
                {"namespace": "docs", "key": "b", "content": "content b", "metadata": {"tag": "test"}},
            ],
        )
        json_str = export.model_dump_json()
        parsed = KnowledgeExportFile.model_validate_json(json_str)
        assert len(parsed.items) == 2
        assert parsed.items[1].metadata == {"tag": "test"}


class TestConfigExport:
    def test_config_export_with_secrets(self):
        """Config export marks contains_encrypted_values when secrets exist."""
        export = ConfigExportFile(
            contains_encrypted_values=True,
            item_count=2,
            items=[
                {"key": "api_url", "value": "https://api.example.com", "config_type": "string"},
                {"key": "api_key", "value": "encrypted-value-abc", "config_type": "secret"},
            ],
        )
        data = json.loads(export.model_dump_json())
        assert data["contains_encrypted_values"] is True
        assert data["items"][1]["config_type"] == "secret"

    def test_config_export_with_integration_ref(self):
        """Config items reference integration by name, not ID."""
        export = ConfigExportFile(
            item_count=1,
            items=[{
                "key": "tenant_id",
                "value": "abc-123",
                "config_type": "string",
                "integration_name": "Microsoft Partner",
            }],
        )
        data = json.loads(export.model_dump_json())
        assert data["items"][0]["integration_name"] == "Microsoft Partner"


class TestTableExport:
    def test_table_export_with_documents(self):
        """Table export includes documents."""
        export = TableExportFile(
            item_count=1,
            items=[{
                "name": "customers",
                "description": "Customer records",
                "schema": {"columns": [{"name": "email", "type": "string"}]},
                "documents": [
                    {"id": "doc-1", "data": {"email": "a@b.com", "name": "Alice"}},
                    {"id": "doc-2", "data": {"email": "c@d.com", "name": "Bob"}},
                ],
            }],
        )
        data = json.loads(export.model_dump_json())
        assert len(data["items"][0]["documents"]) == 2


class TestIntegrationExport:
    def test_integration_export_full(self):
        """Integration export includes schema, mappings, OAuth, and config."""
        export = IntegrationExportFile(
            contains_encrypted_values=True,
            item_count=1,
            items=[{
                "name": "Microsoft Partner",
                "entity_id": "tenant_id",
                "entity_id_name": "Tenant ID",
                "config_schema": [
                    {"key": "api_url", "type": "string", "required": True, "position": 0},
                    {"key": "api_key", "type": "secret", "required": True, "position": 1},
                ],
                "mappings": [
                    {"entity_id": "abc-123", "entity_name": "Contoso", "config": {"api_url": "https://api.contoso.com"}},
                ],
                "oauth_provider": {
                    "provider_name": "microsoft",
                    "client_id": "client-123",
                    "encrypted_client_secret": "encrypted-base64-value",
                    "authorization_url": "https://login.microsoft.com/authorize",
                    "token_url": "https://login.microsoft.com/token",
                    "scopes": ["openid", "profile"],
                },
                "default_config": {"api_url": "https://default-api.example.com"},
            }],
        )
        data = json.loads(export.model_dump_json())
        assert data["items"][0]["name"] == "Microsoft Partner"
        assert len(data["items"][0]["config_schema"]) == 2
        assert data["items"][0]["oauth_provider"]["client_id"] == "client-123"


class TestBulkExport:
    def test_bulk_export_request_model(self):
        """BulkExportRequest accepts optional ID lists."""
        req = BulkExportRequest(
            knowledge_ids=["id1", "id2"],
            config_ids=["id3"],
        )
        assert len(req.knowledge_ids) == 2
        assert len(req.table_ids) == 0
        assert len(req.config_ids) == 1


class TestImportModels:
    def test_import_result_model(self):
        """ImportResult tracks created/updated/skipped/errors."""
        result = ImportResult(
            entity_type="knowledge",
            created=5,
            updated=2,
            skipped=1,
            details=[
                ImportResultItem(name="docs/intro", status="created"),
                ImportResultItem(name="docs/faq", status="error", error="Invalid content"),
            ],
        )
        assert result.created == 5
        assert result.details[1].error == "Invalid content"


class TestOrganizationNameBackwardsCompat:
    """Verify old exports (without organization_name) parse correctly."""

    def test_knowledge_without_org_name(self):
        """Old knowledge export without organization_name field parses with None."""
        old_json = json.dumps({
            "bifrost_export_version": "1.0",
            "entity_type": "knowledge",
            "item_count": 1,
            "items": [{
                "namespace": "docs",
                "key": "intro",
                "content": "Hello",
                "metadata": {},
                "organization_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            }],
        })
        parsed = KnowledgeExportFile.model_validate_json(old_json)
        assert len(parsed.items) == 1
        assert parsed.items[0].organization_name is None
        assert parsed.items[0].organization_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_table_without_org_name(self):
        """Old table export without organization_name parses with None."""
        old_json = json.dumps({
            "bifrost_export_version": "1.0",
            "entity_type": "tables",
            "item_count": 1,
            "items": [{"name": "t1", "organization_id": "abc-123", "documents": []}],
        })
        parsed = TableExportFile.model_validate_json(old_json)
        assert parsed.items[0].organization_name is None

    def test_config_without_org_name(self):
        """Old config export without organization_name parses with None."""
        old_json = json.dumps({
            "bifrost_export_version": "1.0",
            "entity_type": "configs",
            "item_count": 1,
            "items": [{"key": "k", "value": "v", "config_type": "string"}],
        })
        parsed = ConfigExportFile.model_validate_json(old_json)
        assert parsed.items[0].organization_name is None

    def test_integration_mapping_without_org_name(self):
        """Old integration mapping without organization_name parses with None."""
        old_json = json.dumps({
            "bifrost_export_version": "1.0",
            "entity_type": "integrations",
            "item_count": 1,
            "items": [{
                "name": "Test",
                "config_schema": [],
                "mappings": [{"entity_id": "e1", "organization_id": "org-1", "config": {}}],
                "default_config": {},
            }],
        })
        parsed = IntegrationExportFile.model_validate_json(old_json)
        assert parsed.items[0].mappings[0].organization_name is None


class TestOrganizationNameSerialization:
    """Verify organization_name appears in export JSON when set."""

    def test_knowledge_with_org_name(self):
        """Knowledge export item includes organization_name in JSON."""
        item = KnowledgeExportItem(
            namespace="docs",
            content="Hello",
            organization_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            organization_name="Contoso",
        )
        data = json.loads(item.model_dump_json())
        assert data["organization_name"] == "Contoso"

    def test_table_with_org_name(self):
        """Table export item includes organization_name in JSON."""
        item = TableExportItem(name="t1", organization_name="Acme Corp")
        data = json.loads(item.model_dump_json())
        assert data["organization_name"] == "Acme Corp"

    def test_config_with_org_name(self):
        """Config export item includes organization_name in JSON."""
        item = ConfigExportItem(
            key="k", value="v", config_type="string",
            organization_name="Contoso",
        )
        data = json.loads(item.model_dump_json())
        assert data["organization_name"] == "Contoso"

    def test_mapping_with_org_name(self):
        """Integration mapping export item includes organization_name in JSON."""
        item = IntegrationMappingExportItem(
            entity_id="e1",
            organization_id="org-1",
            organization_name="Contoso",
        )
        data = json.loads(item.model_dump_json())
        assert data["organization_name"] == "Contoso"

    def test_oauth_provider_with_org_name(self):
        """OAuth provider export item includes organization_name in JSON."""
        item = OAuthProviderExportItem(
            provider_name="ms",
            client_id="c1",
            encrypted_client_secret="secret",
            organization_name="Contoso",
        )
        data = json.loads(item.model_dump_json())
        assert data["organization_name"] == "Contoso"

    def test_oauth_provider_includes_provider_metadata(self):
        """OAuth provider export item carries provider behavior metadata."""
        item = OAuthProviderExportItem(
            provider_name="scope-sensitive",
            client_id="c1",
            encrypted_client_secret="secret",
            provider_metadata={"omit_token_exchange_scope": True},
        )
        data = json.loads(item.model_dump_json())
        assert data["provider_metadata"] == {"omit_token_exchange_scope": True}

    def test_knowledge_roundtrip_with_org_name(self):
        """Knowledge export with org_name survives serialization roundtrip."""
        export = KnowledgeExportFile(
            item_count=1,
            items=[{
                "namespace": "docs",
                "content": "Hello",
                "organization_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "organization_name": "Contoso",
            }],
        )
        json_str = export.model_dump_json()
        parsed = KnowledgeExportFile.model_validate_json(json_str)
        assert parsed.items[0].organization_name == "Contoso"


class TestParseTargetOrg:
    """Unit tests for _parse_target_org helper."""

    def test_none_returns_resolve_from_file(self):
        """None input means resolve from file."""
        override, force_global = _parse_target_org(None)
        assert override is None
        assert force_global is False

    def test_empty_string_returns_force_global(self):
        """Empty string means force global scope."""
        override, force_global = _parse_target_org("")
        assert override is None
        assert force_global is True

    def test_uuid_string_returns_override(self):
        """UUID string returns the parsed UUID."""
        test_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        override, force_global = _parse_target_org(test_uuid)
        assert override == UUID(test_uuid)
        assert force_global is False

    def test_invalid_uuid_raises(self):
        """Invalid UUID string raises ValueError."""
        with pytest.raises(ValueError):
            _parse_target_org("not-a-uuid")


class TestJsonResponse:
    @pytest.mark.asyncio
    async def test_json_response_sets_download_headers_and_body(self):
        response = _json_response('{"ok": true}', "export.json")

        assert response.media_type == "application/json"
        assert response.headers["content-disposition"] == (
            'attachment; filename="export.json"'
        )

        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        assert body == b'{"ok": true}'


class TestResolveOrgId:
    @pytest.mark.asyncio
    async def test_force_global_wins_without_db_lookup(self):
        warnings: list[str] = []
        db = _FakeDb([])

        resolved = await _resolve_org_id(
            db,
            item_org_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            item_org_name="Contoso",
            target_org_override=None,
            force_global=True,
            warnings=warnings,
            item_label="item",
        )

        assert resolved is None
        assert warnings == []
        assert db.executed == []

    @pytest.mark.asyncio
    async def test_override_wins_without_db_lookup(self):
        warnings: list[str] = []
        override = UUID("11111111-1111-1111-1111-111111111111")
        db = _FakeDb([])

        resolved = await _resolve_org_id(
            db,
            item_org_id=None,
            item_org_name="Contoso",
            target_org_override=override,
            force_global=False,
            warnings=warnings,
            item_label="item",
        )

        assert resolved == override
        assert warnings == []
        assert db.executed == []

    @pytest.mark.asyncio
    async def test_resolves_by_organization_name_first(self):
        org_id = UUID("22222222-2222-2222-2222-222222222222")
        warnings: list[str] = []
        db = _FakeDb([org_id])

        resolved = await _resolve_org_id(
            db,
            item_org_id="33333333-3333-3333-3333-333333333333",
            item_org_name="Contoso",
            target_org_override=None,
            force_global=False,
            warnings=warnings,
            item_label="item",
        )

        assert resolved == org_id
        assert warnings == []
        assert len(db.executed) == 1

    @pytest.mark.asyncio
    async def test_invalid_uuid_falls_back_to_global_with_warning(self):
        warnings: list[str] = []
        db = _FakeDb([None])

        resolved = await _resolve_org_id(
            db,
            item_org_id="not-a-uuid",
            item_org_name="Missing",
            target_org_override=None,
            force_global=False,
            warnings=warnings,
            item_label="config/api_key",
        )

        assert resolved is None
        assert "invalid organization_id" in warnings[0]
        assert "config/api_key" in warnings[0]

    @pytest.mark.asyncio
    async def test_existing_uuid_resolves_after_name_miss(self):
        org_id = UUID("44444444-4444-4444-4444-444444444444")
        warnings: list[str] = []
        db = _FakeDb([None, org_id])

        resolved = await _resolve_org_id(
            db,
            item_org_id=str(org_id),
            item_org_name="Missing",
            target_org_override=None,
            force_global=False,
            warnings=warnings,
            item_label="item",
        )

        assert resolved == org_id
        assert warnings == []
        assert len(db.executed) == 2

    @pytest.mark.asyncio
    async def test_missing_uuid_falls_back_to_global_with_warning(self):
        org_id = "55555555-5555-5555-5555-555555555555"
        warnings: list[str] = []
        db = _FakeDb([None, None])

        resolved = await _resolve_org_id(
            db,
            item_org_id=org_id,
            item_org_name="Missing",
            target_org_override=None,
            force_global=False,
            warnings=warnings,
            item_label="table/customers",
        )

        assert resolved is None
        assert "organization not found" in warnings[0]
        assert "table/customers" in warnings[0]


class TestImportKnowledge:
    @pytest.mark.asyncio
    async def test_import_knowledge_creates_placeholder_document_and_warning(self):
        file = _UploadFile(
            KnowledgeExportFile(
                item_count=1,
                items=[
                    KnowledgeExportItem(
                        namespace="docs",
                        key="intro",
                        content="Hello",
                        metadata={"source": "unit"},
                    )
                ],
            ).model_dump_json()
        )
        db = _FakeDb([None])
        user = SimpleNamespace(user_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))

        result = await import_knowledge(
            db,
            user,
            file,
            target_organization_id=None,
        )

        assert result.created == 1
        assert result.updated == 0
        assert result.details[0].name == "docs/intro"
        assert result.details[0].status == "created"
        assert "placeholder embeddings" in result.warnings[0]
        assert db.committed is True
        assert len(db.added) == 1
        added = db.added[0]
        assert added.namespace == "docs"
        assert added.key == "intro"
        assert added.content == "Hello"
        assert added.doc_metadata == {"source": "unit"}
        assert added.embedding == [0.0] * 1536

    @pytest.mark.asyncio
    async def test_import_knowledge_updates_or_skips_existing_document(self):
        existing = SimpleNamespace(content="old", doc_metadata={})
        file = _UploadFile(
            KnowledgeExportFile(
                item_count=1,
                items=[
                    KnowledgeExportItem(
                        namespace="docs",
                        key="intro",
                        content="New body",
                        metadata={"v": 2},
                    )
                ],
            ).model_dump_json()
        )
        db = _FakeDb([existing])
        user = SimpleNamespace(user_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))

        result = await import_knowledge(
            db,
            user,
            file,
            replace_existing=True,
            target_organization_id=None,
        )

        assert result.updated == 1
        assert existing.content == "New body"
        assert existing.doc_metadata == {"v": 2}

        existing = SimpleNamespace(content="old", doc_metadata={})
        db = _FakeDb([existing])
        result = await import_knowledge(
            db,
            user,
            _UploadFile(file.content),
            replace_existing=False,
            target_organization_id=None,
        )

        assert result.skipped == 1
        assert existing.content == "old"

    @pytest.mark.asyncio
    async def test_import_knowledge_rejects_invalid_file(self):
        with pytest.raises(Exception) as exc:
            await import_knowledge(
                _FakeDb([]),
                SimpleNamespace(user_id="user-1"),
                _UploadFile("{bad"),
                target_organization_id=None,
            )

        assert getattr(exc.value, "status_code", None) == 400


class TestImportHelpers:
    @pytest.mark.asyncio
    async def test_sync_config_schema_updates_adds_and_deletes_items(self):
        existing_keep = SimpleNamespace(
            key="api_url",
            type="string",
            required=False,
            description="old",
            options=None,
            position=99,
        )
        stale = SimpleNamespace(key="old_key")
        integration = SimpleNamespace(
            id=UUID("11111111-1111-1111-1111-111111111111"),
            config_schema=[existing_keep, stale],
        )
        db = _FakeDb([])

        await _sync_config_schema(
            db,
            integration,
            [
                ConfigSchemaExportItem(
                    key="api_url",
                    type="secret",
                    required=True,
                    description="new",
                    options=["a"],
                    position=1,
                ),
                ConfigSchemaExportItem(
                    key="tenant",
                    type="string",
                    required=False,
                    position=2,
                ),
            ],
        )

        assert existing_keep.type == "secret"
        assert existing_keep.required is True
        assert existing_keep.description == "new"
        assert existing_keep.options == ["a"]
        assert existing_keep.position == 1
        assert len(db.added) == 1
        assert db.added[0].key == "tenant"
        assert db.deleted == [stale]

    @pytest.mark.asyncio
    async def test_import_config_value_updates_existing_and_falls_back_on_bad_schema_type(self):
        integration_id = UUID("22222222-2222-2222-2222-222222222222")
        existing = SimpleNamespace(value={}, config_type=None, updated_by=None)
        schema = SimpleNamespace(id=UUID("33333333-3333-3333-3333-333333333333"), type="not-valid")
        db = _FakeDb([schema, existing])
        result = ImportResult(entity_type="configs")

        await _import_config_value(
            db,
            integration_id,
            None,
            "api_url",
            "https://example.test",
            None,
            "user-1",
            result,
        )

        assert existing.value == {"value": "https://example.test"}
        assert existing.config_type == ConfigType.STRING
        assert existing.updated_by == "user-1"
        assert db.added == []

    @pytest.mark.asyncio
    async def test_import_config_value_adds_new_secret_and_warns_on_reencrypt_failure(
        self,
        monkeypatch,
    ):
        integration_id = UUID("22222222-2222-2222-2222-222222222222")
        schema = SimpleNamespace(id=UUID("33333333-3333-3333-3333-333333333333"), type="secret")
        db = _FakeDb([schema, None])
        result = ImportResult(entity_type="configs")
        monkeypatch.setattr(export_import, "decrypt_with_key", lambda *_: (_ for _ in ()).throw(ValueError("bad key")))

        await _import_config_value(
            db,
            integration_id,
            UUID("44444444-4444-4444-4444-444444444444"),
            "api_key",
            "ciphertext",
            "source-key",
            "user-1",
            result,
        )

        assert "Could not re-encrypt secret" in result.warnings[0]
        assert len(db.added) == 1
        assert db.added[0].key == "api_key"
        assert db.added[0].config_type == ConfigType.SECRET

    @pytest.mark.asyncio
    async def test_import_oauth_provider_updates_existing_provider(self):
        existing = SimpleNamespace(
            provider_name="old",
            display_name=None,
            oauth_flow_type=None,
            client_id=None,
            encrypted_client_secret=None,
            authorization_url=None,
            token_url=None,
            token_url_defaults=None,
            redirect_uri=None,
            scopes=None,
            provider_metadata=None,
        )
        integration = SimpleNamespace(
            id=UUID("55555555-5555-5555-5555-555555555555"),
            name="Demo",
            oauth_provider=existing,
        )
        db = _FakeDb([])
        result = ImportResult(entity_type="integrations")

        await _import_oauth_provider(
            db,
            integration,
            OAuthProviderExportItem(
                provider_name="ms",
                display_name="Microsoft",
                client_id="client-1",
                encrypted_client_secret="c2VjcmV0",
                authorization_url="https://login/authorize",
                token_url="https://login/token",
                token_url_defaults={"resource": "graph"},
                redirect_uri="https://app/callback",
                scopes=["openid"],
                provider_metadata={"omit_token_exchange_scope": True},
            ),
            source_secret_key=None,
            result=result,
        )

        assert existing.provider_name == "ms"
        assert existing.display_name == "Microsoft"
        assert existing.encrypted_client_secret == b"secret"
        assert existing.provider_metadata == {"omit_token_exchange_scope": True}
        assert db.added == []

    @pytest.mark.asyncio
    async def test_import_oauth_provider_creates_new_provider_and_warns_on_bad_reencrypt(
        self,
        monkeypatch,
    ):
        integration = SimpleNamespace(
            id=UUID("55555555-5555-5555-5555-555555555555"),
            name="Demo",
            oauth_provider=None,
        )
        db = _FakeDb([])
        result = ImportResult(entity_type="integrations")
        monkeypatch.setattr(export_import, "derive_fernet_key_for_oauth", lambda *_: (_ for _ in ()).throw(ValueError("bad key")))

        await _import_oauth_provider(
            db,
            integration,
            OAuthProviderExportItem(
                provider_name="ms",
                client_id="client-1",
                encrypted_client_secret="c2VjcmV0",
            ),
            source_secret_key="source-key",
            result=result,
            force_global=True,
        )

        assert "Could not re-encrypt OAuth client secret" in result.warnings[0]
        assert len(db.added) == 1
        assert db.added[0].provider_name == "ms"
        assert db.added[0].encrypted_client_secret == b"secret"


class TestImportConfigs:
    @pytest.mark.asyncio
    async def test_import_configs_requires_source_key_for_encrypted_exports(self):
        file = _UploadFile(
            ConfigExportFile(
                contains_encrypted_values=True,
                item_count=1,
                items=[
                    ConfigExportItem(
                        key="api_key",
                        value="ciphertext",
                        config_type="secret",
                    )
                ],
            ).model_dump_json()
        )

        with pytest.raises(Exception) as exc:
            await import_configs(
                _FakeDb([]),
                SimpleNamespace(user_id="user-1"),
                file,
                source_secret_key=None,
                target_organization_id=None,
            )

        assert getattr(exc.value, "status_code", None) == 400

    @pytest.mark.asyncio
    async def test_import_configs_creates_string_config_and_warns_missing_integration(self):
        file = _UploadFile(
            ConfigExportFile(
                item_count=1,
                items=[
                    ConfigExportItem(
                        key="tenant_id",
                        value="tenant-1",
                        config_type="string",
                        description="Tenant",
                        integration_name="Missing Integration",
                    )
                ],
            ).model_dump_json()
        )
        db = _FakeDb([None, None])
        user = SimpleNamespace(user_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))

        result = await import_configs(
            db,
            user,
            file,
            target_organization_id=None,
        )

        assert result.created == 1
        assert "Integration 'Missing Integration' not found" in result.warnings[0]
        assert len(db.added) == 1
        added = db.added[0]
        assert added.key == "tenant_id"
        assert added.value == {"value": "tenant-1"}
        assert added.config_type == ConfigType.STRING
        assert added.integration_id is None
        assert added.updated_by == str(user.user_id)
        assert db.committed is True

    @pytest.mark.asyncio
    async def test_import_configs_updates_or_skips_existing_config(self):
        existing = SimpleNamespace(
            value={"value": "old"},
            config_type=ConfigType.STRING,
            description="old",
            updated_by=None,
        )
        file = _UploadFile(
            ConfigExportFile(
                item_count=1,
                items=[
                    ConfigExportItem(
                        key="api_url",
                        value="https://new.example",
                        config_type="json",
                        description="new",
                    )
                ],
            ).model_dump_json()
        )
        user = SimpleNamespace(user_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))

        result = await import_configs(
            _FakeDb([existing]),
            user,
            file,
            replace_existing=True,
            target_organization_id=None,
        )

        assert result.updated == 1
        assert existing.value == {"value": "https://new.example"}
        assert existing.config_type == ConfigType.JSON
        assert existing.description == "new"
        assert existing.updated_by == str(user.user_id)

        existing = SimpleNamespace(
            value={"value": "old"},
            config_type=ConfigType.STRING,
            description="old",
            updated_by=None,
        )
        result = await import_configs(
            _FakeDb([existing]),
            user,
            _UploadFile(file.content),
            replace_existing=False,
            target_organization_id=None,
        )

        assert result.skipped == 1
        assert existing.value == {"value": "old"}

    @pytest.mark.asyncio
    async def test_import_configs_records_reencrypt_error_and_continues(self, monkeypatch):
        file = _UploadFile(
            ConfigExportFile(
                contains_encrypted_values=True,
                item_count=1,
                items=[
                    ConfigExportItem(
                        key="api_key",
                        value="ciphertext",
                        config_type="secret",
                    )
                ],
            ).model_dump_json()
        )
        monkeypatch.setattr(
            export_import,
            "decrypt_with_key",
            lambda *_: (_ for _ in ()).throw(ValueError("bad key")),
        )

        result = await import_configs(
            _FakeDb([]),
            SimpleNamespace(user_id="user-1"),
            file,
            source_secret_key="source-key",
            target_organization_id=None,
        )

        assert result.errors == 1
        assert "Failed to re-encrypt secret" in result.details[0].error


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def all(self):
        return self.value


class _FakeDb:
    def __init__(self, values: list[object]):
        self.values = values
        self.executed = []
        self.added = []
        self.deleted = []
        self.committed = False
        self.flushed = 0

    async def execute(self, query):
        self.executed.append(query)
        return _FakeResult(self.values.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True

    async def flush(self):
        self.flushed += 1


class _UploadFile:
    def __init__(self, content: str | bytes):
        self.content = content.encode() if isinstance(content, str) else content

    async def read(self):
        return self.content
