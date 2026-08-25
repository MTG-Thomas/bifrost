from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from src.models.contracts.llm import (
    EmbeddingConfigRequest,
    EmbeddingReindexResponse,
    EmbeddingTestRequest,
)
from src.routers import llm_config


def _user(email: str = "admin@example.com") -> SimpleNamespace:
    return SimpleNamespace(email=email, user_id="user-1")


def _db() -> AsyncMock:
    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


class TestEmbeddingConfigEndpoints:
    def test_normalize_endpoint_collapses_empty_and_openai_default(self):
        assert llm_config._normalize_endpoint(None) is None
        assert llm_config._normalize_endpoint("") is None
        assert llm_config._normalize_endpoint("https://api.openai.com/v1/") is None
        assert llm_config._normalize_endpoint("https://example.test/v1/") == (
            "https://example.test/v1"
        )

    @pytest.mark.asyncio
    async def test_get_embedding_config_reports_saved_dedicated_config(self):
        db = _db()
        connection_id = uuid4()
        connection = SimpleNamespace(
            provider="openai",
            endpoint="https://example.test/v1",
            encrypted_api_key="ciphertext",
        )
        config = SimpleNamespace(
            connection_id=connection_id,
            connection=connection,
            model="text-embedding-3-large",
            dimensions=3072,
        )
        service = MagicMock()
        service.get_embedding_config_row = AsyncMock(return_value=config)
        service.embedding_client_endpoint.return_value = connection.endpoint

        with patch.object(llm_config, "AIModelService", return_value=service):
            result = await llm_config.get_embedding_config_endpoint(db, _user())

        assert result.connection_id == connection_id
        assert result.model == "text-embedding-3-large"
        assert result.dimensions == 3072
        assert result.api_key_set is True
        assert result.uses_llm_key is False

    @pytest.mark.asyncio
    async def test_get_embedding_config_reports_unconfigured_without_saved_row(self):
        db = _db()
        service = MagicMock()
        service.get_embedding_config_row = AsyncMock(return_value=None)

        with patch.object(llm_config, "AIModelService", return_value=service):
            result = await llm_config.get_embedding_config_endpoint(db, _user())

        assert result.is_configured is False
        assert result.model == ""
        assert result.api_key_set is False

    @pytest.mark.asyncio
    async def test_embedding_test_with_api_key_lists_models(self):
        request = EmbeddingTestRequest(
            api_key="sk-test",
            endpoint="https://example.test/v1/",
        )

        with (
            patch(
                "src.services.embeddings.url_safety.validate_embedding_endpoint",
                return_value="https://example.test/v1",
            ),
            patch.object(
                llm_config,
                "_list_embedding_models",
                AsyncMock(return_value=["text-embedding-3-small"]),
            ) as list_models,
        ):
            result = await llm_config.test_embedding_connection(request, _db(), _user())

        assert result.success is True
        assert result.models == ["text-embedding-3-small"]
        list_models.assert_awaited_once_with("sk-test", "https://example.test/v1")

    @pytest.mark.asyncio
    async def test_embedding_test_rejects_bad_endpoint_before_listing_models(self):
        request = EmbeddingTestRequest(
            api_key="sk-test",
            endpoint="https://internal.test/v1",
        )

        with (
            patch(
                "src.services.embeddings.url_safety.validate_embedding_endpoint",
                side_effect=ValueError("private address"),
            ),
            patch.object(llm_config, "_list_embedding_models", AsyncMock()) as list_models,
        ):
            result = await llm_config.test_embedding_connection(request, _db(), _user())

        assert result.success is False
        assert "Endpoint rejected" in result.message
        list_models.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_embedding_config_raises_not_found_without_saved_row(self):
        db = _db()
        service = MagicMock()
        service.delete_embedding_config = AsyncMock(return_value=False)

        with patch.object(llm_config, "AIModelService", return_value=service):
            with pytest.raises(HTTPException) as exc:
                await llm_config.delete_embedding_config(db, _user())

        assert exc.value.status_code == status.HTTP_404_NOT_FOUND
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_embedding_config_deletes_saved_row(self):
        db = _db()
        service = MagicMock()
        service.delete_embedding_config = AsyncMock(return_value=True)

        with patch.object(llm_config, "AIModelService", return_value=service):
            await llm_config.delete_embedding_config(db, _user())

        service.delete_embedding_config.assert_awaited_once()
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_embedding_test_returns_no_key_without_saved_or_inherited_key(self):
        db = _db()
        service = MagicMock()
        service.get_embedding_config_row = AsyncMock(return_value=None)

        with patch.object(llm_config, "AIModelService", return_value=service):
            result = await llm_config.test_embedding_connection(
                EmbeddingTestRequest(api_key=None, endpoint=None),
                db,
                _user(),
            )

        assert result.success is False
        assert result.message == "No API key provided and no saved key found"

    @pytest.mark.asyncio
    async def test_trigger_embedding_reindex_requires_saved_embedding_config(self):
        db = _db()
        service = MagicMock()
        service.get_embedding_config_row = AsyncMock(return_value=None)

        with patch.object(llm_config, "AIModelService", return_value=service):
            with pytest.raises(HTTPException) as exc:
                await llm_config.trigger_embedding_reindex(db, _user())

        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "No embedding configuration" in exc.value.detail

    @pytest.mark.asyncio
    async def test_trigger_embedding_reindex_returns_noop_when_store_empty(self):
        db = _db()
        service = MagicMock()
        service.get_embedding_config_row = AsyncMock(
            return_value=SimpleNamespace(
                model="text-embedding-3-small",
                dimensions=1536,
            )
        )

        with (
            patch.object(llm_config, "AIModelService", return_value=service),
            patch(
                "src.services.embeddings.reindex.count_knowledge_rows",
                AsyncMock(return_value=0),
            ),
        ):
            result = await llm_config.trigger_embedding_reindex(db, _user())

        assert isinstance(result, EmbeddingReindexResponse)
        assert result.notification_id == ""
        assert result.row_count == 0

    @pytest.mark.asyncio
    async def test_list_embedding_models_filters_capability_aware_payload(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "data": [
                        {
                            "id": "chat-only",
                            "architecture": {"output_modalities": ["text"]},
                        },
                        {
                            "id": "embedder",
                            "architecture": {"output_modalities": ["embeddings"]},
                        },
                        {"id": "bad-modalities", "architecture": {"output_modalities": "embeddings"}},
                        {"id": 123},
                    ]
                }

        class Client:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, *args, **kwargs):
                return Response()

        with (
            patch(
                "src.services.embeddings.url_safety.validate_embedding_endpoint",
                return_value="https://provider.test/v1",
            ),
            patch("httpx.AsyncClient", Client),
        ):
            result = await llm_config._list_embedding_models(
                "sk-test",
                "https://provider.test/v1/",
            )

        assert result == ["embedder"]

    @pytest.mark.asyncio
    async def test_set_embedding_config_rejects_endpoint_before_live_call(self):
        db = _db()
        connection_id = uuid4()
        connection = SimpleNamespace(
            provider="openai",
            endpoint="https://internal.test/v1",
            encrypted_api_key="ciphertext",
        )
        service = MagicMock()
        service.get_embedding_config_row = AsyncMock(return_value=None)
        service.get_connection = AsyncMock(return_value=connection)
        service.client_provider.return_value = "openai"
        service.decrypt_api_key.return_value = "sk-test"
        service.embedding_client_endpoint.return_value = connection.endpoint

        with (
            patch.object(llm_config, "AIModelService", return_value=service),
            patch(
                "src.services.embeddings.url_safety.validate_embedding_endpoint",
                side_effect=ValueError("private address"),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await llm_config.set_embedding_config(
                    EmbeddingConfigRequest(
                        connection_id=connection_id,
                        model="text-embedding-3-small",
                    ),
                    db,
                    _user(),
                )

        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Embedding endpoint rejected" in exc.value.detail
