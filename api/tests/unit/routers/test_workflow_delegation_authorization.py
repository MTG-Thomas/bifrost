from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.security import ENGINE_USER_ID, decode_token, mint_engine_token
from src.routers.workflows import _validate_execution_identity_overrides


def _context(*, user_id: str, org_id: UUID | None, is_superuser: bool = True):
    return SimpleNamespace(
        user=SimpleNamespace(
            user_id=UUID(user_id),
            is_superuser=is_superuser,
        ),
        org_id=org_id,
    )


def test_engine_token_is_bound_to_parent_execution_org() -> None:
    org_id = uuid4()
    token, _ = mint_engine_token(organization_id=str(org_id))

    payload = decode_token(token)

    assert payload is not None
    assert payload["sub"] == ENGINE_USER_ID
    assert payload["org_id"] == str(org_id)


def test_engine_delegation_allows_only_matching_org() -> None:
    org_id = uuid4()
    ctx = _context(user_id=ENGINE_USER_ID, org_id=org_id)

    _validate_execution_identity_overrides(ctx, org_id=str(org_id), run_as=None)

    with pytest.raises(HTTPException, match="cannot override"):
        _validate_execution_identity_overrides(ctx, org_id=str(uuid4()), run_as=None)


def test_engine_delegation_cannot_impersonate_user() -> None:
    ctx = _context(user_id=ENGINE_USER_ID, org_id=uuid4())

    with pytest.raises(HTTPException, match="cannot override"):
        _validate_execution_identity_overrides(ctx, org_id=None, run_as=str(uuid4()))


def test_platform_admin_api_call_retains_override_access() -> None:
    ctx = _context(user_id=str(uuid4()), org_id=uuid4(), is_superuser=True)

    _validate_execution_identity_overrides(
        ctx,
        org_id=str(uuid4()),
        run_as=str(uuid4()),
    )
