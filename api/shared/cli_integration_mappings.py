"""Shared CLI integration mapping scope helpers."""

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import DeveloperContext


def _parse_scope_uuid(scope: str) -> UUID:
    try:
        return UUID(scope)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"scope must be 'global', a UUID, or null; got {scope!r}",
        ) from None


async def _resolve_mapping_org_id(
    current_user: Any,
    scope: str | None,
    db: AsyncSession,
) -> UUID | None:
    user_org_id = current_user.organization_id

    if not current_user.is_superuser:
        if user_org_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CLI scope requires an organization",
            )
        if scope == "global":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only superusers can access global CLI scope",
            )
        if scope and _parse_scope_uuid(scope) != user_org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot access another organization's CLI scope",
            )
        return user_org_id

    if scope == "global":
        return None

    if scope:
        requested_org_id = _parse_scope_uuid(scope)
        if user_org_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Use global scope for platform-wide integration mappings",
            )
        return requested_org_id

    if user_org_id is not None:
        return user_org_id

    stmt = select(DeveloperContext).where(DeveloperContext.user_id == current_user.user_id)
    result = await db.execute(stmt)
    dev_ctx = result.scalar_one_or_none()

    if dev_ctx and dev_ctx.default_org_id:
        return dev_ctx.default_org_id

    return None


async def list_cli_integration_mappings(
    repo: Any,
    current_user: Any,
    scope: str | None,
    integration_id: UUID,
    db: AsyncSession,
) -> list[Any]:
    """List mappings visible to a CLI mapping request."""
    org_id = await _resolve_mapping_org_id(current_user, scope, db)
    if org_id is None and scope == "global":
        return await repo.list_mappings(integration_id)
    if org_id is None:
        return []
    return await repo.list_mappings(integration_id, organization_id=org_id)


async def get_cli_integration_mapping(
    repo: Any,
    current_user: Any,
    scope: str | None,
    integration_id: UUID,
    db: AsyncSession,
    entity_id: str | None,
) -> Any | None:
    """Return the CLI-visible mapping selected by scope or entity id."""
    mappings = await list_cli_integration_mappings(
        repo,
        current_user,
        scope,
        integration_id,
        db,
    )
    if entity_id:
        return next((mapping for mapping in mappings if mapping.entity_id == entity_id), None)
    return mappings[0] if mappings else None
