"""
Organization Validation Helper

Provides consistent organization validation for write operations across endpoints.
Ensures that entities are only assigned to organizations that exist and are active.

This prevents:
- Assigning entities to non-existent organizations
- Assigning entities to inactive/disabled organizations
- Silent data integrity issues from invalid org_id assignments
"""

import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.organizations import Organization

logger = logging.getLogger(__name__)


async def validate_org_assignment(
    db: AsyncSession,
    organization_id: UUID | str | None,
    *,
    entity_label: str = "entity",
) -> UUID | None:
    """
    Validate that an organization exists and is active before assigning an entity to it.

    Use this whenever an endpoint accepts an ``organization_id`` in a request body
    and intends to write it to a database row (create or update). It ensures that
    the referenced organization is real and usable.

    Args:
        db: Async database session.
        organization_id: The org ID from the request (UUID, string, or None).
            If None, the entity is treated as global and no validation is needed.
        entity_label: Human-readable name of the entity being assigned,
            used in error messages (e.g. "workflow", "event source").

    Returns:
        The validated UUID if an org was provided, or None for global scope.

    Raises:
        HTTPException 400: If organization_id is not a valid UUID.
        HTTPException 404: If the organization does not exist.
        HTTPException 400: If the organization exists but is inactive.
    """
    if organization_id is None:
        return None

    # Parse string to UUID if needed
    if isinstance(organization_id, str):
        try:
            organization_id = UUID(organization_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid organization_id for {entity_label}: not a valid UUID",
            )

    result = await db.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization '{organization_id}' not found — "
            f"cannot assign {entity_label} to a non-existent organization",
        )

    if not org.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Organization '{org.name}' is inactive — "
            f"cannot assign {entity_label} to a disabled organization",
        )

    return organization_id
