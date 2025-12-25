"""
Configuration SDK for Bifrost - API-only implementation.

Provides Python API for configuration management (get, set, list, delete).
All operations go through HTTP API endpoints.
All methods are async and must be awaited.
"""

from __future__ import annotations

from typing import Any

from .client import get_client
from .models import ConfigData


class config:
    """
    Configuration management operations.

    Allows workflows to read and write configuration values scoped to organizations.
    All operations are performed via HTTP API endpoints.

    All methods are async - await is required.
    """

    @staticmethod
    async def get(key: str, org_id: str | None = None, default: Any = None) -> Any:
        """
        Get configuration value with automatic secret decryption.

        Calls SDK API endpoint to retrieve configuration.

        Args:
            key: Configuration key
            org_id: Organization ID (defaults to current org from context)
            default: Default value if key not found (optional)

        Returns:
            Any: Configuration value, or default if not found

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import config
            >>> api_key = await config.get("api_key")
            >>> timeout = await config.get("timeout", default=30)
        """
        client = get_client()
        response = await client.post(
            "/api/cli/config/get",
            json={"key": key, "org_id": org_id}
        )

        if response.status_code == 200:
            result = response.json()
            if result is None:
                return default
            return result.get("value", default)
        else:
            return default

    @staticmethod
    async def set(key: str, value: Any, org_id: str | None = None, is_secret: bool = False) -> None:
        """
        Set configuration value.

        Calls SDK API endpoint to store configuration (writes directly to database).

        Args:
            key: Configuration key
            value: Configuration value (must be JSON-serializable)
            org_id: Organization ID (defaults to current org from context)
            is_secret: If True, encrypts the value before storage

        Raises:
            RuntimeError: If not authenticated
            ValueError: If value is not JSON-serializable

        Example:
            >>> from bifrost import config
            >>> await config.set("api_url", "https://api.example.com")
            >>> await config.set("api_key", "secret123", is_secret=True)
        """
        client = get_client()
        response = await client.post(
            "/api/cli/config/set",
            json={"key": key, "value": value, "org_id": org_id, "is_secret": is_secret}
        )
        response.raise_for_status()

    @staticmethod
    async def list(org_id: str | None = None) -> ConfigData:
        """
        List configuration key-value pairs.

        Note: Secret values are shown as the decrypted value (or "[SECRET]" on error).

        Args:
            org_id: Organization ID (optional, defaults to current org)

        Returns:
            ConfigData: Configuration data with dot-notation and dict-like access:
                >>> cfg = await config.list()
                >>> cfg.api_url        # Dot notation access
                >>> cfg["api_url"]     # Dict-like access
                >>> "api_url" in cfg   # Containment check
                >>> cfg.keys()         # Iterate keys

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import config
            >>> cfg = await config.list()
            >>> api_url = cfg.api_url
            >>> timeout = cfg.timeout or 30
        """
        client = get_client()
        response = await client.post(
            "/api/cli/config/list",
            json={"org_id": org_id}
        )
        response.raise_for_status()
        return ConfigData.model_validate({"data": response.json()})

    @staticmethod
    async def delete(key: str, org_id: str | None = None) -> bool:
        """
        Delete configuration value.

        Calls SDK API endpoint to delete configuration (deletes directly from database).

        Args:
            key: Configuration key
            org_id: Organization ID (defaults to current org from context)

        Returns:
            bool: True if deleted successfully

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import config
            >>> await config.delete("old_api_url")
        """
        client = get_client()
        response = await client.post(
            "/api/cli/config/delete",
            json={"key": key, "org_id": org_id}
        )
        response.raise_for_status()
        return response.json()
