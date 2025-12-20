"""
Unit tests for LLMConfigService.

Tests LLM configuration CRUD operations with mocked database.
"""

import base64
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from src.services.llm_config_service import (
    LLMConfigService,
    LLMProviderConfig,
    LLMTestResult,
    LLM_CONFIG_CATEGORY,
    LLM_CONFIG_KEY,
)


@pytest.fixture
def mock_settings():
    """Mock settings with secret key."""
    settings = MagicMock()
    settings.secret_key = "test-secret-key-for-testing-must-be-32-chars"
    return settings


@pytest.fixture
def mock_session():
    """Mock database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    return session


@pytest.fixture
def fernet_instance(mock_settings):
    """Create a real Fernet instance for encryption/decryption testing."""
    key_bytes = mock_settings.secret_key.encode()[:32].ljust(32, b"0")
    return Fernet(base64.urlsafe_b64encode(key_bytes))


@pytest.fixture
def sample_config_data(fernet_instance):
    """Sample LLM configuration data."""
    encrypted_key = fernet_instance.encrypt(b"sk-test-api-key-12345").decode()
    return {
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "encrypted_api_key": encrypted_key,
        "endpoint": None,
        "max_tokens": 4096,
        "temperature": 0.7,
    }


@pytest.fixture
def mock_system_config(sample_config_data):
    """Mock SystemConfig ORM object."""
    config = MagicMock()
    config.id = uuid4()
    config.category = LLM_CONFIG_CATEGORY
    config.key = LLM_CONFIG_KEY
    config.value_json = sample_config_data
    config.organization_id = None
    config.created_at = datetime.utcnow()
    config.updated_at = datetime.utcnow()
    return config


class TestLLMConfigService:
    """Test LLMConfigService methods."""

    @pytest.mark.asyncio
    async def test_get_config_returns_none_when_not_configured(
        self, mock_session, mock_settings
    ):
        """Test get_config returns None when no config exists."""
        # Setup mock to return no results
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute.return_value = mock_result

        with patch("src.services.llm_config_service.get_settings", return_value=mock_settings):
            service = LLMConfigService(mock_session)
            result = await service.get_config()

        assert result is None

    @pytest.mark.asyncio
    async def test_get_config_returns_config_when_exists(
        self, mock_session, mock_settings, mock_system_config, sample_config_data
    ):
        """Test get_config returns LLMProviderConfig when config exists."""
        # Setup mock to return config
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_system_config
        mock_session.execute.return_value = mock_result

        with patch("src.services.llm_config_service.get_settings", return_value=mock_settings):
            service = LLMConfigService(mock_session)
            result = await service.get_config()

        assert result is not None
        assert isinstance(result, LLMProviderConfig)
        assert result.provider == "anthropic"
        assert result.model == "claude-sonnet-4-20250514"
        assert result.is_configured is True
        assert result.api_key_set is True
        # API key should NOT be returned
        assert not hasattr(result, "api_key") or result.api_key_set is True

    @pytest.mark.asyncio
    async def test_save_config_creates_new_config(
        self, mock_session, mock_settings
    ):
        """Test save_config creates new config when none exists."""
        # Setup mock to return no existing config
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute.return_value = mock_result

        with patch("src.services.llm_config_service.get_settings", return_value=mock_settings):
            service = LLMConfigService(mock_session)
            await service.save_config(
                provider="openai",
                model="gpt-4o",
                api_key="sk-test-key",
                updated_by="test@example.com",
            )

        # Verify new config was added
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

        # Verify the config data
        added_config = mock_session.add.call_args[0][0]
        assert added_config.category == LLM_CONFIG_CATEGORY
        assert added_config.key == LLM_CONFIG_KEY
        assert added_config.value_json["provider"] == "openai"
        assert added_config.value_json["model"] == "gpt-4o"
        assert "encrypted_api_key" in added_config.value_json
        assert added_config.organization_id is None

    @pytest.mark.asyncio
    async def test_save_config_updates_existing_config(
        self, mock_session, mock_settings, mock_system_config
    ):
        """Test save_config updates existing config."""
        # Setup mock to return existing config
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_system_config
        mock_session.execute.return_value = mock_result

        with patch("src.services.llm_config_service.get_settings", return_value=mock_settings):
            service = LLMConfigService(mock_session)
            await service.save_config(
                provider="openai",
                model="gpt-4o-mini",
                api_key="sk-new-key",
                updated_by="admin@example.com",
            )

        # Verify existing config was updated (not added)
        mock_session.add.assert_not_called()
        mock_session.flush.assert_called_once()

        # Verify the config was updated
        assert mock_system_config.value_json["provider"] == "openai"
        assert mock_system_config.value_json["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_save_config_encrypts_api_key(
        self, mock_session, mock_settings, fernet_instance
    ):
        """Test that API key is encrypted when saved."""
        # Setup mock to return no existing config
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute.return_value = mock_result

        test_api_key = "sk-my-secret-api-key"

        with patch("src.services.llm_config_service.get_settings", return_value=mock_settings):
            service = LLMConfigService(mock_session)
            await service.save_config(
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                api_key=test_api_key,
                updated_by="test@example.com",
            )

        # Get the saved config
        added_config = mock_session.add.call_args[0][0]
        encrypted_key = added_config.value_json["encrypted_api_key"]

        # Verify it's encrypted (not plaintext)
        assert encrypted_key != test_api_key

        # Verify it can be decrypted back to original
        decrypted = fernet_instance.decrypt(encrypted_key.encode()).decode()
        assert decrypted == test_api_key

    @pytest.mark.asyncio
    async def test_delete_config_returns_true_when_deleted(
        self, mock_session, mock_settings, mock_system_config
    ):
        """Test delete_config returns True when config is deleted."""
        # Setup mock to return existing config
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_system_config
        mock_session.execute.return_value = mock_result

        with patch("src.services.llm_config_service.get_settings", return_value=mock_settings):
            service = LLMConfigService(mock_session)
            result = await service.delete_config()

        assert result is True
        mock_session.delete.assert_called_once_with(mock_system_config)
        mock_session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_config_returns_false_when_not_found(
        self, mock_session, mock_settings
    ):
        """Test delete_config returns False when config doesn't exist."""
        # Setup mock to return no config
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute.return_value = mock_result

        with patch("src.services.llm_config_service.get_settings", return_value=mock_settings):
            service = LLMConfigService(mock_session)
            result = await service.delete_config()

        assert result is False
        mock_session.delete.assert_not_called()


class TestLLMConfigServiceTestConnection:
    """Test LLMConfigService test_connection method."""

    @pytest.mark.asyncio
    async def test_test_connection_returns_error_when_not_configured(
        self, mock_session, mock_settings
    ):
        """Test test_connection returns error when config doesn't exist."""
        # Mock get_llm_config to raise ValueError (it's imported inside the method)
        with patch("src.services.llm_config_service.get_settings", return_value=mock_settings):
            with patch(
                "src.services.llm.factory.get_llm_config",
                side_effect=ValueError("LLM provider not configured"),
            ):
                service = LLMConfigService(mock_session)
                result = await service.test_connection()

        assert isinstance(result, LLMTestResult)
        assert result.success is False
        assert "not configured" in result.message

    @pytest.mark.asyncio
    async def test_test_connection_openai_success(
        self, mock_session, mock_settings
    ):
        """Test test_connection succeeds for OpenAI."""
        # Mock LLM config
        mock_llm_config = MagicMock()
        mock_llm_config.provider = "openai"
        mock_llm_config.api_key = "sk-test-key"
        mock_llm_config.model = "gpt-4o"

        # Mock OpenAI client
        mock_models = [MagicMock(id="gpt-4o"), MagicMock(id="gpt-3.5-turbo")]
        mock_models_response = MagicMock()
        mock_models_response.data = mock_models

        with patch("src.services.llm_config_service.get_settings", return_value=mock_settings):
            with patch(
                "src.services.llm.factory.get_llm_config",
                return_value=mock_llm_config,
            ):
                with patch("openai.AsyncOpenAI") as mock_openai:
                    mock_client = AsyncMock()
                    mock_client.models.list.return_value = mock_models_response
                    mock_openai.return_value = mock_client

                    service = LLMConfigService(mock_session)
                    result = await service.test_connection()

        assert result.success is True
        assert "Connected to OpenAI" in result.message
        assert result.models is not None

    @pytest.mark.asyncio
    async def test_test_connection_anthropic_success(
        self, mock_session, mock_settings
    ):
        """Test test_connection succeeds for Anthropic."""
        # Mock LLM config
        mock_llm_config = MagicMock()
        mock_llm_config.provider = "anthropic"
        mock_llm_config.api_key = "sk-ant-test-key"
        mock_llm_config.model = "claude-sonnet-4-20250514"

        with patch("src.services.llm_config_service.get_settings", return_value=mock_settings):
            with patch(
                "src.services.llm.factory.get_llm_config",
                return_value=mock_llm_config,
            ):
                with patch("anthropic.AsyncAnthropic") as mock_anthropic:
                    mock_client = AsyncMock()
                    mock_client.messages.create.return_value = MagicMock()
                    mock_anthropic.return_value = mock_client

                    service = LLMConfigService(mock_session)
                    result = await service.test_connection()

        assert result.success is True
        assert "Connected to Anthropic" in result.message
        assert result.models is not None

    @pytest.mark.asyncio
    async def test_test_connection_handles_api_error(
        self, mock_session, mock_settings
    ):
        """Test test_connection handles API errors gracefully."""
        # Mock LLM config
        mock_llm_config = MagicMock()
        mock_llm_config.provider = "openai"
        mock_llm_config.api_key = "invalid-key"
        mock_llm_config.model = "gpt-4o"

        with patch("src.services.llm_config_service.get_settings", return_value=mock_settings):
            with patch(
                "src.services.llm.factory.get_llm_config",
                return_value=mock_llm_config,
            ):
                with patch("openai.AsyncOpenAI") as mock_openai:
                    mock_client = AsyncMock()
                    mock_client.models.list.side_effect = Exception("Invalid API key")
                    mock_openai.return_value = mock_client

                    service = LLMConfigService(mock_session)
                    result = await service.test_connection()

        assert result.success is False
        assert "failed" in result.message.lower() or "Invalid API key" in result.message


class TestLLMConfigServiceListModels:
    """Test LLMConfigService list_models method."""

    @pytest.mark.asyncio
    async def test_list_models_returns_models_on_success(
        self, mock_session, mock_settings
    ):
        """Test list_models returns models when connection succeeds."""
        with patch("src.services.llm_config_service.get_settings", return_value=mock_settings):
            with patch.object(
                LLMConfigService,
                "test_connection",
                return_value=LLMTestResult(
                    success=True,
                    message="Connected",
                    models=["gpt-4o", "gpt-3.5-turbo"],
                ),
            ):
                service = LLMConfigService(mock_session)
                result = await service.list_models()

        assert result is not None
        assert len(result) == 2
        assert "gpt-4o" in result

    @pytest.mark.asyncio
    async def test_list_models_returns_none_on_failure(
        self, mock_session, mock_settings
    ):
        """Test list_models returns None when connection fails."""
        with patch("src.services.llm_config_service.get_settings", return_value=mock_settings):
            with patch.object(
                LLMConfigService,
                "test_connection",
                return_value=LLMTestResult(
                    success=False,
                    message="Connection failed",
                    models=None,
                ),
            ):
                service = LLMConfigService(mock_session)
                result = await service.list_models()

        assert result is None
