"""Authorization policy for workflow calls made through the engine transport."""

from __future__ import annotations

from uuid import UUID

from src.core.principal import UserPrincipal
from src.core.security import ENGINE_USER_ID


class DelegationAuthorizationError(PermissionError):
    """Raised when an execution attempts to exceed its delegated identity."""


def is_engine_principal(user: UserPrincipal) -> bool:
    """Identify current and pre-marker engine tokens without matching embeds."""
    return bool(getattr(user, "is_engine_token", False)) or (
        str(user.user_id) == ENGINE_USER_ID
        and getattr(user, "email", "") == "engine@bifrost.internal"
    )


def validate_execution_identity_overrides(
    user: UserPrincipal,
    execution_org_id: UUID | None,
    *,
    requested_org_id: str | None,
    run_as: str | None,
) -> None:
    """Keep delegated calls inside the original execution identity."""
    if is_engine_principal(user):
        crosses_org = requested_org_id is not None and requested_org_id != (
            str(execution_org_id) if execution_org_id is not None else None
        )
        if run_as is not None or crosses_org:
            raise DelegationAuthorizationError(
                "Delegated workflows cannot override org_id or run_as"
            )
        return

    if (requested_org_id or run_as) and not user.is_superuser:
        raise DelegationAuthorizationError(
            "org_id and run_as overrides require platform admin"
        )


def effective_execution_user(
    user: UserPrincipal,
    execution_org_id: UUID | None,
) -> UserPrincipal:
    """Return the original caller represented by an engine transport token."""
    if not is_engine_principal(user):
        return user

    if user.delegated_user_id is None:
        raise DelegationAuthorizationError(
            "Delegated workflow is missing original caller identity"
        )

    return UserPrincipal(
        user_id=user.delegated_user_id,
        email=user.delegated_email,
        organization_id=execution_org_id,
        name=user.delegated_name,
        is_active=True,
        is_superuser=user.delegated_is_superuser,
        is_verified=True,
        is_external=user.delegated_is_external,
        is_provider_org=user.delegated_is_provider_org,
    )
