"""
LuxSci direct-tenant API helpers for Bifrost integrations.

This first pass intentionally keeps to account-scoped, read-only inventory:
- users
- domains
- aliases

Auth uses LuxSci's documented account-scope API key header:
  X-API-Key: <public token>:<secret key>

Source:
- https://luxsci.com/api/mechanics/
- https://luxsci.com/rest-api/luxsci-api.yaml (v2025.1.15.1)
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


class LuxSciClient:
    """Focused async client for LuxSci account inventory."""

    BASE_URL = "https://rest.luxsci.com/perl/api/v2"
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        *,
        api_token: str,
        api_secret: str,
        account_id: str,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._base_url = (base_url or self.BASE_URL).rstrip("/")
        self.account_id = account_id
        self._timeout = timeout
        self._max_retries = max_retries
        self._http = httpx.AsyncClient(
            timeout=self._timeout,
            headers={
                "Accept": "application/json",
                "X-API-Key": f"{api_token}:{api_secret}",
            },
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        response: httpx.Response | None = None

        for attempt in range(self._max_retries + 1):
            response = await self._http.request(
                method,
                url,
                params=params or None,
                json=json_body,
            )

            if response.status_code not in self.RETRYABLE_STATUS_CODES:
                break

            if attempt >= self._max_retries:
                break

            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    wait_seconds = float(retry_after)
                except ValueError:
                    wait_seconds = 2**attempt
            else:
                wait_seconds = 2**attempt
            await asyncio.sleep(min(wait_seconds, 30.0))

        assert response is not None
        if not response.is_success:
            body = response.text[:1000]
            raise RuntimeError(
                f"LuxSci [{method.upper()} {path}] HTTP {response.status_code}: {body}"
            )

        if not response.content:
            return {}

        payload = response.json()
        if not isinstance(payload, dict):
            return payload

        success = payload.get("success")
        if success not in (1, "1", True, None):
            raise RuntimeError(
                f"LuxSci [{method.upper()} {path}] returned unsuccessful response: {payload}"
            )

        return payload.get("data", payload)

    async def list_users(
        self,
        *,
        status: str | None = None,
        domain: str | None = None,
        younger_than: int | None = None,
        older_than: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if domain:
            params["domain"] = domain
        if younger_than is not None:
            params["younger_than"] = younger_than
        if older_than is not None:
            params["older_than"] = older_than

        payload = await self._request(
            "GET",
            f"/account/{self.account_id}/users",
            params=params or None,
        )
        return payload if isinstance(payload, list) else []

    async def list_domains(self) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            f"/account/{self.account_id}/domains",
        )
        return payload if isinstance(payload, list) else []

    async def list_aliases(self) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            f"/account/{self.account_id}/aliases",
        )
        return payload if isinstance(payload, list) else []

    @staticmethod
    def normalize_user(user: dict[str, Any]) -> dict[str, Any]:
        primary_email = user.get("user") or user.get("email1")
        return {
            "id": str(user.get("uid") or primary_email or ""),
            "name": str(user.get("contact") or primary_email or ""),
            "email": str(primary_email or ""),
            "status": str(user.get("status") or ""),
            "domain": str((primary_email or "").split("@", 1)[1] if "@" in str(primary_email or "") else ""),
            "company": str(user.get("company") or ""),
            "services": user.get("services") if isinstance(user.get("services"), list) else [],
        }

    @staticmethod
    def normalize_domain(domain: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(domain.get("id") or domain.get("gid") or domain.get("domain") or ""),
            "name": str(domain.get("domain") or ""),
            "is_enabled": bool(domain.get("is_enabled")),
            "is_verified": bool(domain.get("is_verified")),
            "is_hipaa": bool(domain.get("is_hipaa")),
            "user_count": int(domain.get("users") or 0),
        }

    @staticmethod
    def normalize_alias(alias: dict[str, Any]) -> dict[str, Any]:
        alias_address = ""
        user = str(alias.get("user") or "")
        domain = str(alias.get("domain") or "")
        if user and domain:
            alias_address = f"{user}@{domain}"
        return {
            "id": alias_address or str(alias.get("created") or ""),
            "address": alias_address,
            "status": str(alias.get("status") or ""),
            "action": str(alias.get("action") or ""),
            "destination": str(alias.get("dest") or ""),
            "type": str(alias.get("type") or ""),
        }


async def get_client(scope: str | None = None) -> LuxSciClient:
    """Build a LuxSci client from the configured Bifrost integration."""
    from bifrost import integrations

    integration = await integrations.get("LuxSci", scope=scope)
    if not integration:
        raise RuntimeError("Integration 'LuxSci' not found in Bifrost")

    config = integration.config or {}
    required = ["api_token", "api_secret", "account_id"]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise RuntimeError(f"LuxSci integration missing required config: {missing}")

    return LuxSciClient(
        base_url=config.get("base_url") or LuxSciClient.BASE_URL,
        api_token=str(config["api_token"]),
        api_secret=str(config["api_secret"]),
        account_id=str(config["account_id"]),
    )
