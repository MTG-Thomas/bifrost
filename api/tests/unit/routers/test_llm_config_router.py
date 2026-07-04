from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

from src.models.contracts.llm import (
    EmbeddingTestRequest,
    LLMConfigRequest,
    LLMTestRequest,
)
from src.routers import llm_config


def _user(email: str = "admin@example.com") -> SimpleNamespace:
    return SimpleNamespace(email=email, user_id="user-1")


def _model(model_id: str, display_name: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=model_id, display_name=display_name or model_id)


def _db() -> AsyncMock:
    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _scalar_first_result(value: object) -> MagicMock:
    scalars = MagicMock()
    scalars.first.return_value = value
    result = MagicMock()
    result.scalars.return_value = scalars
    return result


class TestLLMConfigEndpoints:
    @pytest.mark.asyncio
    async def test_get_llm_config_returns_none_when_unconfigured(self):
        service = MagicMock()
        service.get_config = AsyncMock(return_value=None)

        with patch.object(llm_config, "LLMConfigService", return_value=service):
            result = await llm_config.get_llm_config(_db(), _user())

        assert result is None

    @pytest.mark.asyncio
    async def test_get_llm_config_hides_secret_and_preserves_metadata(self):
        config = SimpleNamespace(
            provider="openai",
            model="gpt-4o",
            endpoint="https://example.test/v1",
            max_tokens=4096,
            default_system_prompt="Be useful",
            summarization_model="gpt-4o-mini",
            tuning_model="gpt-4o",
            is_configured=True,
            api_key_set=True,
        )
        service = MagicMock()
        service.get_config = AsyncMock(return_value=config)

        with patch.object(llm_config, "LLMConfigService", return_value=service):
            result = await llm_config.get_llm_config(_db(), _user())

        assert result.provider == "openai"
        assert result.model == "gpt-4o"
        assert result.api_key_set is True
        assert not hasattr(result, "api_key")

    @pytest.mark.asyncio
    async def test_set_llm_config_rolls_back_when_completion_verification_fails(self):
        db = _db()
        service = MagicMock()
        service.save_config = AsyncMock()
        service.verify_completion = AsyncMock(
            return_value=SimpleNamespace(success=False, message="model denied")
        )

        request = LLMConfigRequest(
            provider="openai",
            model="gpt-4o",
            api_key="sk-test",
            endpoint=None,
        )

        with patch.object(llm_config, "LLMConfigService", return_value=service):
            with pytest.raises(HTTPException) as exc:
                await llm_config.set_llm_config(request, db, _user())

        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "model denied" in exc.value.detail
        db.rollback.assert_awaited_once()
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_llm_config_commits_and_reports_existing_key(self):
        db = _db()
        service = MagicMock()
        service.save_config = AsyncMock()
        service.verify_completion = AsyncMock(
            return_value=SimpleNamespace(success=True, message="ok")
        )
        service.get_config = AsyncMock(return_value=SimpleNamespace(api_key_set=True))

        request = LLMConfigRequest(
            provider="openai",
            model="gpt-4o",
            api_key=None,
            endpoint=None,
            max_tokens=2048,
            default_system_prompt="Default",
        )

        with patch.object(llm_config, "LLMConfigService", return_value=service):
            result = await llm_config.set_llm_config(request, db, _user())

        assert result.model == "gpt-4o"
        assert result.api_key_set is True
        db.flush.assert_awaited_once()
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_llm_config_raises_not_found(self):
        service = MagicMock()
        service.delete_config = AsyncMock(return_value=False)

        with patch.object(llm_config, "LLMConfigService", return_value=service):
            with pytest.raises(HTTPException) as exc:
                await llm_config.delete_llm_config(_db(), _user())

        assert exc.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_test_llm_connection_rolls_back_and_caches_successful_models(self):
        db = _db()
        service = MagicMock()
        service.save_config = AsyncMock()
        service.test_connection = AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                message="ok",
                models=[_model("gpt-4o", "GPT 4o")],
            )
        )
        request = LLMTestRequest(
            provider="openai",
            model="gpt-4o",
            api_key="sk-test",
            endpoint=None,
        )

        with (
            patch.object(llm_config, "LLMConfigService", return_value=service),
            patch.object(
                llm_config,
                "_cache_model_mapping_from_result",
                AsyncMock(),
            ) as cache_mapping,
        ):
            result = await llm_config.test_llm_connection(request, db, _user())

        assert result.success is True
        assert result.models[0].id == "gpt-4o"
        db.rollback.assert_awaited_once()
        cache_mapping.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_saved_llm_connection_requires_configured_saved_config(self):
        service = MagicMock()
        service.get_config = AsyncMock(return_value=SimpleNamespace(is_configured=False))

        with patch.object(llm_config, "LLMConfigService", return_value=service):
            with pytest.raises(HTTPException) as exc:
                await llm_config.test_saved_llm_connection(_db(), _user())

        assert exc.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_list_llm_models_returns_provider_models(self):
        service = MagicMock()
        service.get_config = AsyncMock(
            return_value=SimpleNamespace(is_configured=True, provider="anthropic")
        )
        service.list_models = AsyncMock(
            return_value=[_model("claude-sonnet-4-20250514", "Claude Sonnet 4")]
        )

        with patch.object(llm_config, "LLMConfigService", return_value=service):
            result = await llm_config.list_llm_models(_db(), _user())

        assert result.provider == "anthropic"
        assert result.models[0].display_name == "Claude Sonnet 4"

    @pytest.mark.asyncio
    async def test_list_llm_models_raises_when_provider_unavailable(self):
        service = MagicMock()
        service.get_config = AsyncMock(
            return_value=SimpleNamespace(is_configured=True, provider="openai")
        )
        service.list_models = AsyncMock(return_value=None)

        with patch.object(llm_config, "LLMConfigService", return_value=service):
            with pytest.raises(HTTPException) as exc:
                await llm_config.list_llm_models(_db(), _user())

        assert exc.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


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
        db.execute = AsyncMock(
            return_value=_scalar_first_result(
                SimpleNamespace(
                    value_json={
                        "model": "text-embedding-3-large",
                        "dimensions": 3072,
                        "endpoint": "https://example.test/v1",
                        "encrypted_api_key": "ciphertext",
                    }
                )
            )
        )

        result = await llm_config.get_embedding_config_endpoint(db, _user())

        assert result.model == "text-embedding-3-large"
        assert result.dimensions == 3072
        assert result.api_key_set is True
        assert result.uses_llm_key is False

    @pytest.mark.asyncio
    async def test_get_embedding_config_reports_unconfigured_without_saved_row(self):
        db = _db()
        db.execute = AsyncMock(return_value=_scalar_first_result(None))

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
        db.execute = AsyncMock(return_value=_scalar_first_result(None))

        with pytest.raises(HTTPException) as exc:
            await llm_config.delete_embedding_config(db, _user())

        assert exc.value.status_code == status.HTTP_404_NOT_FOUND
        db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_embedding_config_deletes_saved_row(self):
        existing = SimpleNamespace()
        db = _db()
        db.execute = AsyncMock(return_value=_scalar_first_result(existing))
        db.delete = AsyncMock()

        await llm_config.delete_embedding_config(db, _user())

        db.delete.assert_awaited_once_with(existing)
        db.commit.assert_awaited_once()
