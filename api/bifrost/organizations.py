"""
Organization management SDK for Bifrost.

Provides Python API for organization operations from workflows.

Works in two modes:
1. Platform context (inside workflows): Redis cache + write buffer
2. External context (via dev API key): API calls to /api/organizations endpoints

All methods are async and must be awaited.
"""

from __future__ import annotations

import json as json_module
import logging
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, TypeVar

from ._context import _execution_context

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _from_api_response(cls: type[T], data: dict[str, Any]) -> T:
    """Create a dataclass instance from API response, ignoring unknown fields."""
    known_fields = {f.name for f in fields(cls)}
    filtered = {k: v for k, v in data.items() if k in known_fields}
    return cls(**filtered)


# Local dataclass for Organization (avoids src.* imports)
@dataclass
class Organization:
    """Organization model."""
    id: str
    name: str
    domain: str | None = None
    is_active: bool = True
    created_by: str = "system"
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _is_platform_context() -> bool:
    """Check if running inside platform execution context."""
    return _execution_context.get() is not None


def _get_client():
    """Get the BifrostClient for API calls."""
    from .client import get_client
    return get_client()


def _cache_to_schema(cache_data: dict[str, Any]) -> Organization:
    """Convert cached org data to Organization dataclass."""
    now = datetime.utcnow()
    return Organization(
        id=cache_data.get("id", ""),
        name=cache_data.get("name", ""),
        domain=cache_data.get("domain"),
        is_active=cache_data.get("is_active", True),
        created_by=cache_data.get("created_by") or "system",
        created_at=cache_data.get("created_at") or now,
        updated_at=cache_data.get("updated_at") or now,
    )


class organizations:
    """
    Organization management operations.

    Reads from Redis cache, writes to buffer (flushed post-execution).
    All methods enforce permissions via the execution context.

    All methods are async - await is required.
    """

    @staticmethod
    async def create(name: str, domain: str | None = None, is_active: bool = True) -> Organization:
        """
        Create a new organization.

        Requires: Platform admin privileges

        Args:
            name: Organization name
            domain: Organization domain (optional)
            is_active: Whether the organization is active (default: True)

        Returns:
            Organization: Created organization object

        Raises:
            PermissionError: If user is not platform admin
            ValueError: If validation fails
            RuntimeError: If no execution context (platform mode) or missing env vars (external mode)

        Example:
            >>> from bifrost import organizations
            >>> org = await organizations.create("Acme Corp", domain="acme.com")
        """
        if _is_platform_context():
            # Platform mode: write to buffer
            from ._internal import require_admin
            from ._write_buffer import get_write_buffer

            context = require_admin()

            buffer = get_write_buffer()
            org_id = await buffer.add_org_change(
                operation="create",
                org_id=None,
                data={
                    "name": name,
                    "domain": domain,
                    "is_active": is_active,
                },
            )

            # Return schema with generated ID
            now = datetime.utcnow()
            return Organization(
                id=org_id,
                name=name,
                domain=domain,
                is_active=is_active,
                created_by=context.user_id,
                created_at=now,
                updated_at=now,
            )
        else:
            # External mode: call API
            client = _get_client()
            response = await client.post(
                "/api/organizations",
                json={
                    "name": name,
                    "domain": domain,
                    "is_active": is_active,
                }
            )
            response.raise_for_status()
            data = response.json()
            # Convert to local Organization dataclass
            return _from_api_response(Organization, data)

    @staticmethod
    async def get(org_id: str) -> Organization:
        """
        Get organization by ID.

        In platform mode: Reads from Redis cache (pre-warmed).
        In external mode: Calls /api/organizations/{id} endpoint.

        Args:
            org_id: Organization ID

        Returns:
            Organization: Organization object

        Raises:
            ValueError: If organization not found
            RuntimeError: If no execution context (platform mode) or missing env vars (external mode)

        Example:
            >>> from bifrost import organizations
            >>> org = await organizations.get("org-123")
            >>> print(org.name)
        """
        if _is_platform_context():
            # Platform mode: read from cache
            from ._internal import get_context
            from src.core.cache import get_redis, org_key

            get_context()  # Validates user is authenticated

            async with get_redis() as r:
                data = await r.get(org_key(org_id))  # type: ignore[misc]

                if not data:
                    raise ValueError(f"Organization not found: {org_id}")

                try:
                    cache_data = json_module.loads(data)
                    return _cache_to_schema(cache_data)
                except json_module.JSONDecodeError:
                    raise ValueError(f"Invalid organization data: {org_id}")
        else:
            # External mode: call API
            client = _get_client()
            response = await client.get(f"/api/organizations/{org_id}")
            if response.status_code == 404:
                raise ValueError(f"Organization not found: {org_id}")
            response.raise_for_status()
            data = response.json()
            # Convert to local Organization dataclass
            return _from_api_response(Organization, data)

    @staticmethod
    async def list() -> list[Organization]:
        """
        List all organizations.

        Requires: Platform admin privileges

        In platform mode: Reads from Redis cache (pre-warmed).
        In external mode: Calls /api/organizations endpoint.

        Returns:
            list[Organization]: List of organization objects

        Raises:
            PermissionError: If user is not platform admin
            RuntimeError: If no execution context (platform mode) or missing env vars (external mode)

        Example:
            >>> from bifrost import organizations
            >>> orgs = await organizations.list()
            >>> for org in orgs:
            ...     print(f"{org.name}: {org.domain}")
        """
        if _is_platform_context():
            # Platform mode: read from cache
            from ._internal import require_admin
            from src.core.cache import get_redis, org_key, orgs_list_key

            require_admin()

            async with get_redis() as r:
                org_ids = await r.smembers(orgs_list_key())  # type: ignore[misc]

                if not org_ids:
                    return []

                orgs_list: list[Organization] = []
                for oid in org_ids:
                    data = await r.get(org_key(oid))  # type: ignore[misc]
                    if data:
                        try:
                            cache_data = json_module.loads(data)
                            if cache_data.get("is_active", True):
                                orgs_list.append(_cache_to_schema(cache_data))
                        except json_module.JSONDecodeError:
                            continue

                # Sort by name
                orgs_list.sort(key=lambda o: o.name or "")

                return orgs_list
        else:
            # External mode: call API
            client = _get_client()
            response = await client.get("/api/organizations")
            response.raise_for_status()
            data = response.json()
            # Convert to local Organization dataclass
            return [_from_api_response(Organization, org) for org in data]

    @staticmethod
    async def update(org_id: str, **updates: Any) -> Organization:
        """
        Update an organization.

        Requires: Platform admin privileges

        Args:
            org_id: Organization ID
            **updates: Fields to update (name, domain, is_active)

        Returns:
            Organization: Updated organization object

        Raises:
            PermissionError: If user is not platform admin
            ValueError: If organization not found or validation fails
            RuntimeError: If no execution context (platform mode) or missing env vars (external mode)

        Example:
            >>> from bifrost import organizations
            >>> org = await organizations.update("org-123", name="New Name")
        """
        if _is_platform_context():
            # Platform mode: write to buffer
            from ._internal import require_admin
            from ._write_buffer import get_write_buffer
            from src.core.cache import get_redis, org_key

            require_admin()

            # Verify org exists in cache first
            async with get_redis() as r:
                data = await r.get(org_key(org_id))  # type: ignore[misc]
                if not data:
                    raise ValueError(f"Organization not found: {org_id}")

                existing = json_module.loads(data)

            # Apply updates
            updated_data = {
                "name": updates.get("name", existing.get("name")),
                "domain": updates.get("domain", existing.get("domain")),
                "is_active": updates.get("is_active", existing.get("is_active")),
            }

            # Write to buffer
            buffer = get_write_buffer()
            await buffer.add_org_change(
                operation="update",
                org_id=org_id,
                data=updated_data,
            )

            # Return updated schema
            return Organization(
                id=org_id,
                name=updated_data["name"],
                domain=updated_data["domain"],
                is_active=updated_data["is_active"],
                created_by=existing.get("created_by"),
                created_at=existing.get("created_at"),
                updated_at=existing.get("updated_at"),
            )
        else:
            # External mode: call API (uses PATCH)
            client = _get_client()

            # Build request body with only provided updates
            update_payload = {}
            if "name" in updates:
                update_payload["name"] = updates["name"]
            if "domain" in updates:
                update_payload["domain"] = updates["domain"]
            if "is_active" in updates:
                update_payload["is_active"] = updates["is_active"]

            response = await client._http.patch(
                f"/api/organizations/{org_id}",
                json=update_payload
            )
            if response.status_code == 404:
                raise ValueError(f"Organization not found: {org_id}")
            response.raise_for_status()
            data = response.json()
            # Convert to local Organization dataclass
            return _from_api_response(Organization, data)

    @staticmethod
    async def delete(org_id: str) -> bool:
        """
        Delete an organization (soft delete - sets is_active to false).

        Requires: Platform admin privileges

        Args:
            org_id: Organization ID

        Returns:
            bool: True if organization was deleted

        Raises:
            PermissionError: If user is not platform admin
            ValueError: If organization not found
            RuntimeError: If no execution context (platform mode) or missing env vars (external mode)

        Example:
            >>> from bifrost import organizations
            >>> deleted = await organizations.delete("org-123")
        """
        if _is_platform_context():
            # Platform mode: write to buffer
            from ._internal import require_admin
            from ._write_buffer import get_write_buffer
            from src.core.cache import get_redis, org_key

            require_admin()

            # Verify org exists in cache first
            async with get_redis() as r:
                data = await r.get(org_key(org_id))  # type: ignore[misc]
                if not data:
                    raise ValueError(f"Organization not found: {org_id}")

            # Write delete to buffer
            buffer = get_write_buffer()
            await buffer.add_org_change(
                operation="delete",
                org_id=org_id,
                data={},
            )

            return True
        else:
            # External mode: call API
            client = _get_client()
            response = await client.delete(f"/api/organizations/{org_id}")
            if response.status_code == 404:
                raise ValueError(f"Organization not found: {org_id}")
            response.raise_for_status()
            return True
