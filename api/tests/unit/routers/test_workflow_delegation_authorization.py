from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.core.security import ENGINE_USER_ID, decode_token, mint_engine_token
from src.services.execution.delegation_authorization import (
    DelegationAuthorizationError,
    effective_execution_user,
    validate_execution_identity_overrides,
)


def _context(
    *,
    user_id: str,
    org_id: UUID | None,
    is_superuser: bool = True,
    is_engine_token: bool = False,
    email: str = "caller@example.com",
    delegated_user_id: UUID | None = None,
    delegated_is_superuser: bool = False,
    delegated_is_provider_org: bool = False,
    delegated_is_external: bool = False,
):
    return SimpleNamespace(
        user=SimpleNamespace(
            user_id=UUID(user_id),
            is_superuser=is_superuser,
            is_engine_token=is_engine_token,
            delegated_user_id=delegated_user_id,
            email=email,
            delegated_email="caller@example.com",
            delegated_name="Original Caller",
            delegated_is_superuser=delegated_is_superuser,
            delegated_is_provider_org=delegated_is_provider_org,
            delegated_is_external=delegated_is_external,
        ),
        org_id=org_id,
    )


def test_engine_token_is_bound_to_parent_execution_org() -> None:
    org_id = uuid4()
    caller_id = uuid4()
    token, _ = mint_engine_token(
        organization_id=str(org_id),
        delegated_user_id=str(caller_id),
        delegated_email="caller@example.com",
        delegated_name="Original Caller",
        delegated_is_provider_org=True,
        delegated_is_external=False,
    )

    payload = decode_token(token)

    assert payload is not None
    assert payload["sub"] == ENGINE_USER_ID
    assert payload["engine"] is True
    assert payload["org_id"] == str(org_id)
    assert payload["delegated_user_id"] == str(caller_id)
    assert payload["delegated_is_provider_org"] is True
    assert payload["delegated_is_external"] is False


def test_engine_delegation_uses_original_caller_identity() -> None:
    org_id = uuid4()
    caller_id = uuid4()
    ctx = _context(
        user_id=ENGINE_USER_ID,
        org_id=org_id,
        is_engine_token=True,
        delegated_user_id=caller_id,
        delegated_is_provider_org=True,
        delegated_is_external=False,
    )

    effective_user = effective_execution_user(ctx.user, ctx.org_id)

    assert effective_user.user_id == caller_id
    assert effective_user.organization_id == org_id
    assert effective_user.email == "caller@example.com"
    assert effective_user.is_superuser is False
    assert effective_user.is_provider_org is True
    assert effective_user.is_external is False


def test_engine_delegation_preserves_external_non_provider_caller() -> None:
    ctx = _context(
        user_id=ENGINE_USER_ID,
        org_id=uuid4(),
        is_engine_token=True,
        delegated_user_id=uuid4(),
        delegated_is_provider_org=False,
        delegated_is_external=True,
    )

    effective_user = effective_execution_user(ctx.user, ctx.org_id)

    assert effective_user.is_provider_org is False
    assert effective_user.is_external is True


def test_shared_sentinel_subject_without_engine_marker_keeps_own_identity() -> None:
    """Embed sessions share the sentinel subject but are not delegations."""
    ctx = _context(
        user_id=ENGINE_USER_ID,
        org_id=uuid4(),
        is_superuser=False,
        is_engine_token=False,
    )

    assert effective_execution_user(ctx.user, ctx.org_id) is ctx.user


def test_legacy_engine_token_without_marker_still_fails_closed() -> None:
    ctx = _context(
        user_id=ENGINE_USER_ID,
        org_id=uuid4(),
        email="engine@bifrost.internal",
    )

    with pytest.raises(DelegationAuthorizationError, match="missing original caller"):
        effective_execution_user(ctx.user, ctx.org_id)


def test_engine_delegation_without_caller_identity_fails_closed() -> None:
    ctx = _context(
        user_id=ENGINE_USER_ID,
        org_id=uuid4(),
        is_engine_token=True,
    )

    with pytest.raises(DelegationAuthorizationError, match="missing original caller"):
        effective_execution_user(ctx.user, ctx.org_id)


def test_engine_delegation_allows_only_matching_org() -> None:
    org_id = uuid4()
    ctx = _context(
        user_id=ENGINE_USER_ID,
        org_id=org_id,
        is_engine_token=True,
    )

    validate_execution_identity_overrides(
        ctx.user,
        ctx.org_id,
        requested_org_id=str(org_id),
        run_as=None,
    )

    other_org_id = str(uuid4())
    with pytest.raises(DelegationAuthorizationError, match="cannot override"):
        validate_execution_identity_overrides(
            ctx.user,
            ctx.org_id,
            requested_org_id=other_org_id,
            run_as=None,
        )


def test_engine_delegation_cannot_impersonate_user() -> None:
    ctx = _context(
        user_id=ENGINE_USER_ID,
        org_id=uuid4(),
        is_engine_token=True,
    )

    run_as = str(uuid4())
    with pytest.raises(DelegationAuthorizationError, match="cannot override"):
        validate_execution_identity_overrides(
            ctx.user,
            ctx.org_id,
            requested_org_id=None,
            run_as=run_as,
        )


def test_platform_admin_api_call_retains_override_access() -> None:
    ctx = _context(user_id=str(uuid4()), org_id=uuid4(), is_superuser=True)

    validate_execution_identity_overrides(
        ctx.user,
        ctx.org_id,
        requested_org_id=str(uuid4()),
        run_as=str(uuid4()),
    )
