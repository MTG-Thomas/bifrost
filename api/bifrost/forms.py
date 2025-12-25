"""
Forms SDK for Bifrost.

Provides Python API for form operations (read-only).

Works in two modes:
1. Platform context (inside workflows): Redis cache (pre-warmed)
2. External context (via dev API key): API calls to /api/forms endpoints

All methods are async and must be awaited.
"""

from __future__ import annotations

import json as json_module
import logging
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, TypeVar
from uuid import UUID

from ._context import _execution_context

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _from_api_response(cls: type[T], data: dict[str, Any]) -> T:
    """Create a dataclass instance from API response, ignoring unknown fields."""
    known_fields = {f.name for f in fields(cls)}
    filtered = {k: v for k, v in data.items() if k in known_fields}
    return cls(**filtered)


# Local dataclass for FormPublic (avoids src.* imports)
@dataclass
class FormPublic:
    """Form definition."""
    id: UUID
    name: str
    description: str | None = None
    workflow_id: str | None = None
    launch_workflow_id: str | None = None
    default_launch_params: dict[str, Any] | None = None
    allowed_query_params: list[str] | None = None
    form_schema: dict[str, Any] | None = None
    access_level: str | None = None
    organization_id: UUID | None = None
    is_active: bool = True
    file_path: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _is_platform_context() -> bool:
    """Check if running inside platform execution context."""
    return _execution_context.get() is not None


def _get_client():
    """Get the BifrostClient for API calls."""
    from .client import get_client
    return get_client()


class forms:
    """
    Form operations (read-only).

    Allows workflows to list and get form definitions.
    Reads from Redis cache (pre-warmed before execution).

    All methods are async - await is required.
    """

    @staticmethod
    async def list() -> list[FormPublic]:
        """
        List all forms available to the current user.

        In platform mode: Reads from Redis cache (pre-warmed with user's accessible forms).
        In external mode: Calls /api/forms endpoint.

        Returns:
            list[FormPublic]: List of form objects with attributes:
                - id: UUID - Form ID
                - name: str - Form name
                - description: str | None - Form description
                - workflow_id: str | None - Linked workflow ID
                - launch_workflow_id: str | None - Workflow to launch on submit
                - default_launch_params: dict | None - Default params for launch
                - allowed_query_params: list[str] | None - Allowed URL query params
                - form_schema: dict | FormSchema | None - Form field schema
                - access_level: FormAccessLevel | None - Access level
                - organization_id: UUID | None - Organization ID
                - is_active: bool - Whether form is active
                - file_path: str | None - Workspace file path
                - created_at, updated_at: datetime | None

        Raises:
            RuntimeError: If no execution context (platform mode) or missing env vars (external mode)

        Example:
            >>> from bifrost import forms
            >>> all_forms = await forms.list()
            >>> for form in all_forms:
            ...     print(f"{form.id}: {form.name}")
        """
        if _is_platform_context():
            # Platform mode: read from cache
            from src.core.cache import forms_hash_key, get_redis, user_forms_key
            from ._internal import get_context

            context = get_context()

            org_id = None
            if context.org_id and context.org_id != "GLOBAL":
                org_id = context.org_id

            # Read forms from Redis cache (pre-warmed)
            async with get_redis() as r:
                # Get form IDs accessible to this user
                form_ids = await r.smembers(user_forms_key(org_id, context.user_id))  # type: ignore[misc]

                if not form_ids:
                    return []

                # Get form data for each ID
                forms_list: list[FormPublic] = []
                for form_id in form_ids:
                    data = await r.hget(forms_hash_key(org_id), form_id)  # type: ignore[misc]
                    if data:
                        try:
                            form_data = json_module.loads(data)
                            forms_list.append(FormPublic(**form_data))
                        except json_module.JSONDecodeError:
                            continue

                # Sort by name
                forms_list.sort(key=lambda f: f.name or "")

                return forms_list
        else:
            # External mode: call API
            client = _get_client()
            response = await client.get("/api/forms")
            response.raise_for_status()
            data = response.json()
            # Convert to local FormPublic dataclass
            return [_from_api_response(FormPublic, form) for form in data]

    @staticmethod
    async def get(form_id: str) -> FormPublic:
        """
        Get a form definition by ID.

        In platform mode: Reads from Redis cache (pre-warmed).
        In external mode: Calls /api/forms/{id} endpoint.

        Args:
            form_id: Form ID

        Returns:
            FormPublic: Form object with attributes:
                - id: UUID - Form ID
                - name: str - Form name
                - description: str | None - Form description
                - workflow_id: str | None - Linked workflow ID
                - launch_workflow_id: str | None - Workflow to launch on submit
                - default_launch_params: dict | None - Default params for launch
                - allowed_query_params: list[str] | None - Allowed URL query params
                - form_schema: dict | FormSchema | None - Form field schema
                - access_level: FormAccessLevel | None - Access level
                - organization_id: UUID | None - Organization ID
                - is_active: bool - Whether form is active
                - file_path: str | None - Workspace file path
                - created_at, updated_at: datetime | None

        Raises:
            ValueError: If form not found
            PermissionError: If user doesn't have access to the form
            RuntimeError: If no execution context (platform mode) or missing env vars (external mode)

        Example:
            >>> from bifrost import forms
            >>> form = await forms.get("form-123")
            >>> print(form.name)
        """
        if _is_platform_context():
            # Platform mode: read from cache
            from src.core.cache import forms_hash_key, get_redis, user_forms_key
            from ._internal import get_context

            context = get_context()

            org_id = None
            if context.org_id and context.org_id != "GLOBAL":
                org_id = context.org_id

            # Read from Redis cache (pre-warmed)
            async with get_redis() as r:
                # Check if user has access to this form
                if not context.is_platform_admin:
                    form_ids = await r.smembers(user_forms_key(org_id, context.user_id))  # type: ignore[misc]
                    if form_id not in form_ids:
                        raise PermissionError(f"Access denied to form: {form_id}")

                # Get form data
                data = await r.hget(forms_hash_key(org_id), form_id)  # type: ignore[misc]

                if not data:
                    raise ValueError(f"Form not found: {form_id}")

                try:
                    form_data = json_module.loads(data)
                    return FormPublic(**form_data)
                except json_module.JSONDecodeError:
                    raise ValueError(f"Invalid form data: {form_id}")
        else:
            # External mode: call API
            client = _get_client()
            response = await client.get(f"/api/forms/{form_id}")
            if response.status_code == 404:
                raise ValueError(f"Form not found: {form_id}")
            elif response.status_code == 403:
                raise PermissionError(f"Access denied to form: {form_id}")
            response.raise_for_status()
            data = response.json()
            # Convert to local FormPublic dataclass
            return _from_api_response(FormPublic, data)
