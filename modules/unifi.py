"""
UniFi Site Manager + local UniFi Network API helpers for Bifrost integrations.

This module intentionally keeps endpoint paths configurable because UniFi's
public Site Manager API and local Network Application API continue to evolve.

Entity model (default):
  entity_id   = UniFi site ID
  entity_name = site name

Optional mapping config:
  fabric_id   = UniFi fabric ID, when the upstream API includes fabric linkage.
"""

from __future__ import annotations

from typing import Any

import httpx


class UniFiClient:
    """Thin async client for UniFi cloud (Site Manager) and local API surfaces."""

    SITE_MANAGER_BASE_URL = "https://api.ui.com"
    SITE_MANAGER_SITES_PATH = "/v1/sites"
    SITE_MANAGER_FABRICS_PATH = "/v1/fabrics"

    LOCAL_NETWORK_SITES_PATH = "/proxy/network/integration/v1/sites"
    LOCAL_NETWORK_FABRICS_PATH = "/proxy/network/integration/v1/fabrics"

    def __init__(
        self,
        api_key: str,
        *,
        site_manager_base_url: str = SITE_MANAGER_BASE_URL,
        local_network_base_url: str | None = None,
        verify_tls: bool = True,
        timeout: float = 30.0,
        mapped_site_id: str | None = None,
        mapped_fabric_id: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._site_manager_base_url = site_manager_base_url.rstrip("/")
        self._local_network_base_url = (
            local_network_base_url.rstrip("/") if local_network_base_url else None
        )
        self._verify_tls = verify_tls
        self._timeout = timeout
        self._mapped_site_id = str(mapped_site_id or "").strip() or None
        self._mapped_fabric_id = str(mapped_fabric_id or "").strip() or None

        self._site_manager_http: httpx.AsyncClient | None = None
        self._local_network_http: httpx.AsyncClient | None = None

    @property
    def site_id(self) -> str | None:
        return self._mapped_site_id

    @property
    def fabric_id(self) -> str | None:
        return self._mapped_fabric_id

    async def _get_site_manager_http(self) -> httpx.AsyncClient:
        if self._site_manager_http is None:
            self._site_manager_http = httpx.AsyncClient(
                base_url=self._site_manager_base_url,
                headers={
                    "X-API-Key": self._api_key,
                    "Accept": "application/json",
                },
                timeout=self._timeout,
                verify=self._verify_tls,
            )
        return self._site_manager_http

    async def _get_local_network_http(self) -> httpx.AsyncClient:
        if not self._local_network_base_url:
            raise RuntimeError(
                "UniFi local Network API base URL is not configured. "
                "Set 'local_network_base_url' in the UniFi integration config."
            )

        if self._local_network_http is None:
            self._local_network_http = httpx.AsyncClient(
                base_url=self._local_network_base_url,
                headers={
                    "X-API-Key": self._api_key,
                    "Accept": "application/json",
                },
                timeout=self._timeout,
                verify=self._verify_tls,
            )
        return self._local_network_http

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        surface: str = "site_manager",
    ) -> httpx.Response:
        http = (
            await self._get_site_manager_http()
            if surface == "site_manager"
            else await self._get_local_network_http()
        )
        response = await http.request(method, path, params=params or None)
        if not response.is_success:
            body = response.text[:1000]
            raise RuntimeError(
                f"UniFi [{surface} {method.upper()} {path}] HTTP {response.status_code}: {body}"
            )
        return response

    @staticmethod
    def _coerce_list(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        if isinstance(payload, dict):
            for key in ("data", "items", "results", "sites", "fabrics"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def normalize_site(site: dict[str, Any]) -> dict[str, str]:
        site_id = site.get("id") or site.get("siteId") or site.get("site_id")
        name = site.get("name") or site.get("siteName") or site.get("desc")
        fabric_id = (
            site.get("fabricId")
            or site.get("fabric_id")
            or site.get("fabric", {}).get("id")
            if isinstance(site.get("fabric"), dict)
            else site.get("fabricId") or site.get("fabric_id")
        )
        return {
            "id": str(site_id or ""),
            "name": str(name or ""),
            "fabric_id": str(fabric_id or ""),
        }

    @staticmethod
    def normalize_fabric(fabric: dict[str, Any]) -> dict[str, str]:
        fabric_id = fabric.get("id") or fabric.get("fabricId") or fabric.get("fabric_id")
        name = fabric.get("name") or fabric.get("displayName") or fabric.get("slug")
        return {
            "id": str(fabric_id or ""),
            "name": str(name or ""),
        }

    async def list_site_manager_sites(self) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            self.SITE_MANAGER_SITES_PATH,
            surface="site_manager",
        )
        return self._coerce_list(response.json())

    async def list_site_manager_fabrics(self) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            self.SITE_MANAGER_FABRICS_PATH,
            surface="site_manager",
        )
        return self._coerce_list(response.json())

    async def list_local_network_sites(self) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            self.LOCAL_NETWORK_SITES_PATH,
            surface="local_network",
        )
        return self._coerce_list(response.json())

    async def list_local_network_fabrics(self) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            self.LOCAL_NETWORK_FABRICS_PATH,
            surface="local_network",
        )
        return self._coerce_list(response.json())

    async def close(self) -> None:
        if self._site_manager_http is not None:
            await self._site_manager_http.aclose()
            self._site_manager_http = None
        if self._local_network_http is not None:
            await self._local_network_http.aclose()
            self._local_network_http = None


async def get_client(scope: str | None = None) -> UniFiClient:
    """Build a UniFi client from Bifrost integration config and mapping context."""
    from bifrost import integrations

    integration = await integrations.get("UniFi", scope=scope)
    if not integration:
        raise RuntimeError("Integration 'UniFi' not found in Bifrost")

    config = integration.config or {}
    api_key = config.get("api_key")
    if not api_key:
        raise RuntimeError("UniFi integration missing required config: ['api_key']")

    mapping_config = {}
    if getattr(integration, "mapping", None) and getattr(integration.mapping, "config", None):
        mapping_config = integration.mapping.config or {}

    verify_tls_value = str(config.get("verify_tls", "true")).strip().lower()
    verify_tls = verify_tls_value not in {"0", "false", "no", "off"}

    return UniFiClient(
        api_key=api_key,
        site_manager_base_url=config.get(
            "site_manager_base_url", UniFiClient.SITE_MANAGER_BASE_URL
        ),
        local_network_base_url=config.get("local_network_base_url") or None,
        verify_tls=verify_tls,
        mapped_site_id=getattr(integration, "entity_id", None),
        mapped_fabric_id=mapping_config.get("fabric_id") if isinstance(mapping_config, dict) else None,
    )
