from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.constants import PROVIDER_ORG_ID
from src.services import user_provisioning
from src.services.user_provisioning import (
    ProvisioningResult,
    ensure_user_provisioned,
    get_user_roles,
)


@pytest.fixture
def db_session():
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def test_provisioning_result_roles_are_authenticated() -> None:
    result = ProvisioningResult(
        user=SimpleNamespace(),
        is_platform_admin=False,
        organization_id=None,
        was_created=False,
    )

    assert result.roles == ["authenticated"]


@pytest.mark.asyncio
async def test_ensure_user_provisioned_rejects_invalid_email(db_session) -> None:
    with pytest.raises(ValueError, match="Invalid email format"):
        await ensure_user_provisioned(db_session, "not-an-email")


@pytest.mark.asyncio
async def test_ensure_user_provisioned_returns_existing_user(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid4()
    existing = SimpleNamespace(
        is_superuser=True,
        organization_id=org_id,
    )
    user_repo = SimpleNamespace(get_by_email=AsyncMock(return_value=existing))

    monkeypatch.setattr(
        user_provisioning,
        "UserRepository",
        MagicMock(return_value=user_repo),
    )
    monkeypatch.setattr(user_provisioning, "OrganizationRepository", MagicMock())

    result = await ensure_user_provisioned(db_session, "Ada@Example.COM")

    assert result.user is existing
    assert result.is_platform_admin is True
    assert result.organization_id == org_id
    assert result.was_created is False
    user_repo.get_by_email.assert_awaited_once_with("ada@example.com")
    db_session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_user_provisioned_creates_first_user_as_provider_admin(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = SimpleNamespace(organization_id=PROVIDER_ORG_ID)
    user_repo = SimpleNamespace(
        get_by_email=AsyncMock(return_value=None),
        has_any_users=AsyncMock(return_value=False),
        create_user=AsyncMock(return_value=created),
    )

    monkeypatch.setattr(
        user_provisioning,
        "UserRepository",
        MagicMock(return_value=user_repo),
    )
    monkeypatch.setattr(user_provisioning, "OrganizationRepository", MagicMock())

    result = await ensure_user_provisioned(db_session, "first@example.com")

    user_repo.create_user.assert_awaited_once_with(
        email="first@example.com",
        name="first",
        is_superuser=True,
        organization_id=PROVIDER_ORG_ID,
    )
    db_session.commit.assert_awaited_once()
    db_session.refresh.assert_awaited_once_with(created)
    assert result.user is created
    assert result.is_platform_admin is True
    assert result.organization_id == PROVIDER_ORG_ID
    assert result.was_created is True


@pytest.mark.asyncio
async def test_ensure_user_provisioned_creates_domain_matched_org_user(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid4()
    matched_org = SimpleNamespace(id=org_id, name="Example", domain="example.com")
    created = SimpleNamespace(organization_id=org_id)
    user_repo = SimpleNamespace(
        get_by_email=AsyncMock(return_value=None),
        has_any_users=AsyncMock(return_value=True),
        create_user=AsyncMock(return_value=created),
    )
    org_repo = SimpleNamespace(get_by_domain=AsyncMock(return_value=matched_org))

    monkeypatch.setattr(
        user_provisioning,
        "UserRepository",
        MagicMock(return_value=user_repo),
    )
    monkeypatch.setattr(
        user_provisioning,
        "OrganizationRepository",
        MagicMock(return_value=org_repo),
    )

    result = await ensure_user_provisioned(
        db_session,
        "New.User@Example.COM",
        name="New User",
    )

    org_repo.get_by_domain.assert_awaited_once_with("example.com")
    user_repo.create_user.assert_awaited_once_with(
        email="new.user@example.com",
        name="New User",
        is_superuser=False,
        organization_id=org_id,
    )
    assert result.user is created
    assert result.is_platform_admin is False
    assert result.organization_id == org_id
    assert result.was_created is True


@pytest.mark.asyncio
async def test_ensure_user_provisioned_rejects_unknown_domain(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_repo = SimpleNamespace(
        get_by_email=AsyncMock(return_value=None),
        has_any_users=AsyncMock(return_value=True),
    )
    org_repo = SimpleNamespace(get_by_domain=AsyncMock(return_value=None))

    monkeypatch.setattr(
        user_provisioning,
        "UserRepository",
        MagicMock(return_value=user_repo),
    )
    monkeypatch.setattr(
        user_provisioning,
        "OrganizationRepository",
        MagicMock(return_value=org_repo),
    )

    with pytest.raises(ValueError, match="No organization configured"):
        await ensure_user_provisioned(db_session, "orphan@example.net")

    db_session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_user_roles_returns_scalar_role_names(db_session) -> None:
    roles = ["admin", "operator"]
    result = MagicMock()
    result.scalars.return_value.all.return_value = roles
    db_session.execute.return_value = result

    assert await get_user_roles(db_session, uuid4()) == roles
    db_session.execute.assert_awaited_once()
