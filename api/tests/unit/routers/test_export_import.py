"""Tests for export/import models and serialization."""

import json

from src.models.contracts.export_import import (
    BulkExportRequest,
    ConfigExportFile,
    ImportResult,
    ImportResultItem,
    IntegrationExportFile,
    KnowledgeExportFile,
    TableExportFile,
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
