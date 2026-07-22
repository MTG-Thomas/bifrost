from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.security import ENGINE_USER_ID, decode_token, mint_engine_token
from src.routers.workflows import (
    _effective_execution_user,
    _validate_execution_identity_overrides,
)


def _context(
    *,
    user_id: str,
    org_id: UUID | None,
    is_superuser: bool = True,
    is_engine_token: bool = False,
    delegated_user_id: UUID | None = None,
    delegated_is_superuser: bool = False,
):
    return SimpleNamespace(
        user=SimpleNamespace(
            user_id=UUID(user_id),
            is_superuser=is_superuser,
            is_engine_token=is_engine_token,
            delegated_user_id=delegated_user_id,
            delegated_email="caller@example.com",
            delegated_name="Original Caller",
            delegated_is_superuser=delegated_is_superuser,
            delegated_is_provider_org=False,
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
    )

    payload = decode_token(token)

    assert payload is not None
    assert payload["sub"] == ENGINE_USER_ID
    assert payload["engine"] is True
    assert payload["org_id"] == str(org_id)
    assert payload["delegated_user_id"] == str(caller_id)


def test_engine_delegation_uses_original_caller_identity() -> None:
    org_id = uuid4()
    caller_id = uuid4()
    ctx = _context(
        user_id=ENGINE_USER_ID,
        org_id=org_id,
        is_engine_token=True,
        delegated_user_id=caller_id,
    )

    effective_user = _effective_execution_user(ctx)

    assert effective_user.user_id == caller_id
    assert effective_user.organization_id == org_id
    assert effective_user.email == "caller@example.com"
    assert effective_user.is_superuser is False


def test_shared_sentinel_subject_without_engine_marker_keeps_own_identity() -> None:
    """Embed sessions share the sentinel subject but are not delegations."""
    ctx = _context(
        user_id=ENGINE_USER_ID,
        org_id=uuid4(),
        is_superuser=False,
        is_engine_token=False,
    )

    assert _effective_execution_user(ctx) is ctx.user


def test_engine_delegation_without_caller_identity_fails_closed() -> None:
    ctx = _context(
        user_id=ENGINE_USER_ID,
        org_id=uuid4(),
        is_engine_token=True,
    )

    with pytest.raises(HTTPException, match="missing original caller"):
        _effective_execution_user(ctx)


def test_engine_delegation_allows_only_matching_org() -> None:
    org_id = uuid4()
    ctx = _context(
        user_id=ENGINE_USER_ID,
        org_id=org_id,
        is_engine_token=True,
    )

    _validate_execution_identity_overrides(ctx, org_id=str(org_id), run_as=None)

    other_org_id = str(uuid4())
    with pytest.raises(HTTPException, match="cannot override"):
        _validate_execution_identity_overrides(ctx, org_id=other_org_id, run_as=None)


def test_engine_delegation_cannot_impersonate_user() -> None:
    ctx = _context(
        user_id=ENGINE_USER_ID,
        org_id=uuid4(),
        is_engine_token=True,
    )

    run_as = str(uuid4())
    with pytest.raises(HTTPException, match="cannot override"):
        _validate_execution_identity_overrides(ctx, org_id=None, run_as=run_as)


def test_platform_admin_api_call_retains_override_access() -> None:
    ctx = _context(user_id=str(uuid4()), org_id=uuid4(), is_superuser=True)

    _validate_execution_identity_overrides(
        ctx,
        org_id=str(uuid4()),
        run_as=str(uuid4()),
    )
