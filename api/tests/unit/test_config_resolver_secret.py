import pytest
from unittest.mock import patch
from src.core.secret_string import SecretString


class TestConfigResolverSecretString:
    """Test that ConfigResolver returns SecretString for secret configs."""

    @pytest.mark.asyncio
    async def test_get_config_returns_secret_string(self):
        from src.core.config_resolver import ConfigResolver

        resolver = ConfigResolver()
        config_data = {
            "api_key": {"value": "encrypted_value", "type": "secret"},
            "name": {"value": "plain_value", "type": "string"},
        }

        with patch("src.core.security.decrypt_secret", return_value="decrypted-secret"):
            result = await resolver.get_config("org1", "api_key", config_data)

        assert isinstance(result, SecretString)
        assert result.get_secret_value() == "decrypted-secret"
        assert str(result) == "[REDACTED]"

    @pytest.mark.asyncio
    async def test_get_config_plain_not_secret_string(self):
        from src.core.config_resolver import ConfigResolver

        resolver = ConfigResolver()
        config_data = {
            "name": {"value": "plain_value", "type": "string"},
        }

        result = await resolver.get_config("org1", "name", config_data)

        assert not isinstance(result, SecretString)
        assert result == "plain_value"
