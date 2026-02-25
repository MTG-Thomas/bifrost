"""Tests for secret decryption in CLI config/get endpoint."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.security import encrypt_secret, decrypt_secret


class TestConfigGetDecryptsSecrets:
    """Verify that cli_get_config decrypts secret-type config values."""

    @pytest.mark.asyncio
    async def test_get_config_returns_decrypted_secret(self):
        """config/get should return plaintext for secret-type values, not encrypted blobs."""
        from src.routers.cli import cli_get_config
        from src.models.contracts.cli import CLIConfigGetRequest, CLIConfigValue

        plaintext = "my_api_key_12345"
        encrypted = encrypt_secret(plaintext)

        # Simulate what cache warming stores in Redis
        cache_entry = json.dumps({"value": encrypted, "type": "secret"})

        mock_redis = AsyncMock()
        mock_redis.hget = AsyncMock(return_value=cache_entry)
        mock_redis.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_redis.__aexit__ = AsyncMock(return_value=False)

        mock_user = MagicMock()
        mock_user.user_id = "test-user-id"
        mock_user.email = "test@example.com"

        request = CLIConfigGetRequest(key="test_secret")

        with patch("src.routers.cli._get_cli_org_id", new_callable=AsyncMock, return_value="org-123"), \
             patch("src.routers.cli.get_redis", return_value=mock_redis):
            result = await cli_get_config(request=request, current_user=mock_user, db=AsyncMock())

        assert isinstance(result, CLIConfigValue)
        assert result.value == plaintext, (
            f"Expected decrypted plaintext '{plaintext}', got '{result.value}'"
        )
        assert result.config_type == "secret"

    @pytest.mark.asyncio
    async def test_get_config_returns_none_for_corrupt_secret(self):
        """config/get should return None value if secret decryption fails."""
        from src.routers.cli import cli_get_config
        from src.models.contracts.cli import CLIConfigGetRequest, CLIConfigValue

        # Simulate a corrupt encrypted value
        cache_entry = json.dumps({"value": "not-valid-encrypted-data", "type": "secret"})

        mock_redis = AsyncMock()
        mock_redis.hget = AsyncMock(return_value=cache_entry)
        mock_redis.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_redis.__aexit__ = AsyncMock(return_value=False)

        mock_user = MagicMock()
        mock_user.user_id = "test-user-id"

        request = CLIConfigGetRequest(key="bad_secret")

        with patch("src.routers.cli._get_cli_org_id", new_callable=AsyncMock, return_value="org-123"), \
             patch("src.routers.cli.get_redis", return_value=mock_redis):
            result = await cli_get_config(request=request, current_user=mock_user, db=AsyncMock())

        assert isinstance(result, CLIConfigValue)
        assert result.value is None, "Corrupt secret should decrypt to None, not raise"

    @pytest.mark.asyncio
    async def test_get_config_nonsecret_not_decrypted(self):
        """config/get should not attempt decryption on non-secret types."""
        from src.routers.cli import cli_get_config
        from src.models.contracts.cli import CLIConfigGetRequest, CLIConfigValue

        plain_value = "just_a_string"
        cache_entry = json.dumps({"value": plain_value, "type": "string"})

        mock_redis = AsyncMock()
        mock_redis.hget = AsyncMock(return_value=cache_entry)
        mock_redis.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_redis.__aexit__ = AsyncMock(return_value=False)

        mock_user = MagicMock()
        mock_user.user_id = "test-user-id"

        request = CLIConfigGetRequest(key="normal_key")

        with patch("src.routers.cli._get_cli_org_id", new_callable=AsyncMock, return_value="org-123"), \
             patch("src.routers.cli.get_redis", return_value=mock_redis):
            result = await cli_get_config(request=request, current_user=mock_user, db=AsyncMock())

        assert isinstance(result, CLIConfigValue)
        assert result.value == plain_value
        assert result.config_type == "string"


class TestConfigListMasksSecrets:
    """Verify that cli_list_config always masks secret values with [SECRET]."""

    @pytest.mark.asyncio
    async def test_list_config_masks_secret_values(self):
        """config/list should return '[SECRET]' for secret-type values, never the encrypted ciphertext."""
        from src.routers.cli import cli_list_config
        from src.models.contracts.cli import CLIConfigListRequest
        from src.core.security import encrypt_secret

        encrypted = encrypt_secret("real_api_key_value")

        # Redis hgetall returns all config entries
        all_data = {
            "normal_key": json.dumps({"value": "normal_value", "type": "string"}),
            "secret_key": json.dumps({"value": encrypted, "type": "secret"}),
        }

        mock_redis = AsyncMock()
        mock_redis.hgetall = AsyncMock(return_value=all_data)
        mock_redis.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_redis.__aexit__ = AsyncMock(return_value=False)

        mock_user = MagicMock()
        mock_user.user_id = "test-user-id"

        request = CLIConfigListRequest()

        with patch("src.routers.cli._get_cli_org_id", new_callable=AsyncMock, return_value="org-123"), \
             patch("src.routers.cli.get_redis", return_value=mock_redis):
            result = await cli_list_config(request=request, current_user=mock_user, db=AsyncMock())

        assert result["normal_key"] == "normal_value"
        assert result["secret_key"] == "[SECRET]", (
            f"Secret should be masked as '[SECRET]', got '{result['secret_key']}'"
        )
        # Ensure encrypted ciphertext is NOT returned
        assert result["secret_key"] != encrypted

    @pytest.mark.asyncio
    async def test_list_config_masks_empty_secret(self):
        """config/list should return '[SECRET]' even for empty/null secret values."""
        from src.routers.cli import cli_list_config
        from src.models.contracts.cli import CLIConfigListRequest

        all_data = {
            "empty_secret": json.dumps({"value": None, "type": "secret"}),
        }

        mock_redis = AsyncMock()
        mock_redis.hgetall = AsyncMock(return_value=all_data)
        mock_redis.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_redis.__aexit__ = AsyncMock(return_value=False)

        mock_user = MagicMock()
        mock_user.user_id = "test-user-id"

        request = CLIConfigListRequest()

        with patch("src.routers.cli._get_cli_org_id", new_callable=AsyncMock, return_value="org-123"), \
             patch("src.routers.cli.get_redis", return_value=mock_redis):
            result = await cli_list_config(request=request, current_user=mock_user, db=AsyncMock())

        assert result["empty_secret"] == "[SECRET]"


class TestEncryptDecryptRoundtrip:
    """Verify encrypt/decrypt are inverses — the foundational guarantee."""

    def test_roundtrip(self):
        """encrypt then decrypt should return the original value."""
        original = "super_secret_api_key_!@#$%"
        encrypted = encrypt_secret(original)
        assert encrypted != original, "encrypt_secret should not return plaintext"
        decrypted = decrypt_secret(encrypted)
        assert decrypted == original
