import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.models.contracts.user_invites import InviteStatus
from src.services import user_invite_service
from src.services.user_invite_service import (
    InviteConsumeError,
    UserInviteService,
    _hash_token,
)


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value


@pytest.fixture
def session():
    db = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    return db


def _invite(**overrides):
    now = datetime.now(timezone.utc)
    data = {
        "user_id": uuid4(),
        "consumed_at": None,
        "revoked_at": None,
        "expires_at": now + timedelta(days=1),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_hash_token_is_stable_sha256_hex_digest() -> None:
    expected = hashlib.sha256(b"invite-token").hexdigest()
    assert _hash_token("invite-token") == expected
    assert _hash_token("invite-token") != "invite-token"
    assert len(_hash_token("invite-token")) == 64


@pytest.mark.asyncio
async def test_create_or_replace_deletes_existing_invite_and_stores_hashed_token(
    session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    creator_id = uuid4()
    existing = _invite(user_id=user_id)
    service = UserInviteService(session)
    service._get_for_user = AsyncMock(return_value=existing)  # type: ignore[method-assign]
    monkeypatch.setattr(user_invite_service.secrets, "token_urlsafe", lambda size: "raw-token")

    raw, invite = await service.create_or_replace(
        user_id=user_id,
        created_by=creator_id,
    )

    assert raw == "raw-token"
    session.delete.assert_awaited_once_with(existing)
    session.add.assert_called_once_with(invite)
    assert invite.user_id == user_id
    assert invite.created_by == creator_id
    assert invite.token_hash == _hash_token("raw-token")
    assert session.flush.await_count == 2


@pytest.mark.asyncio
async def test_revoke_deletes_existing_invite(session) -> None:
    existing = _invite()
    service = UserInviteService(session)
    service._get_for_user = AsyncMock(return_value=existing)  # type: ignore[method-assign]

    await service.revoke(user_id=existing.user_id)

    session.delete.assert_awaited_once_with(existing)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoke_missing_invite_is_noop(session) -> None:
    service = UserInviteService(session)
    service._get_for_user = AsyncMock(return_value=None)  # type: ignore[method-assign]

    await service.revoke(user_id=uuid4())

    session.delete.assert_not_awaited()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_valid_invite_user_rejects_invalid_invite_states(session) -> None:
    service = UserInviteService(session)
    cases = [
        (None, "Invite not found"),
        (_invite(consumed_at=datetime.now(timezone.utc)), "Invite already consumed"),
        (_invite(revoked_at=datetime.now(timezone.utc)), "Invite revoked"),
        (_invite(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)), "Invite expired"),
    ]

    for invite, message in cases:
        session.execute.reset_mock()
        session.execute.return_value = _ScalarResult(invite)
        with pytest.raises(InviteConsumeError, match=message):
            await service.get_valid_invite_user(token="raw")


@pytest.mark.asyncio
async def test_get_valid_invite_user_rejects_already_registered_user(session) -> None:
    invite = _invite()
    user = SimpleNamespace(id=invite.user_id, is_active=True, is_registered=True)
    session.execute.side_effect = [_ScalarResult(invite), _ScalarResult(user)]
    service = UserInviteService(session)

    with pytest.raises(InviteConsumeError, match="already registered"):
        await service.get_valid_invite_user(token="raw")


@pytest.mark.asyncio
async def test_get_valid_invite_user_rejects_inactive_user(session) -> None:
    invite = _invite()
    user = SimpleNamespace(id=invite.user_id, is_active=False, is_registered=False)
    session.execute.side_effect = [_ScalarResult(invite), _ScalarResult(user)]
    service = UserInviteService(session)

    with pytest.raises(InviteConsumeError, match="inactive"):
        await service.get_valid_invite_user(token="raw")


@pytest.mark.asyncio
async def test_get_valid_invite_user_returns_invite_and_unregistered_user(session) -> None:
    invite = _invite()
    user = SimpleNamespace(id=invite.user_id, is_active=True, is_registered=False)
    session.execute.side_effect = [
        _ScalarResult(invite),
        _ScalarResult(user),
        _ScalarResult(None),
    ]

    result = await UserInviteService(session).get_valid_invite_user(token="raw")

    assert result == (invite, user)


@pytest.mark.asyncio
async def test_consume_sets_password_registration_and_consumed_at(
    session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invite = _invite()
    user = SimpleNamespace(is_registered=False, hashed_password=None)
    service = UserInviteService(session)
    service.get_valid_invite_user = AsyncMock(  # type: ignore[method-assign]
        return_value=(invite, user)
    )
    monkeypatch.setattr(user_invite_service, "get_password_hash", lambda password: "hashed")

    result = await service.consume(token="raw", password="secret")

    assert result is user
    assert user.is_registered is True
    assert user.hashed_password == "hashed"
    assert invite.consumed_at is not None
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_status_for_reports_registered_oauth_and_invite_states(session) -> None:
    user = SimpleNamespace(id=uuid4(), is_registered=True)
    service = UserInviteService(session)
    assert await service.status_for(user) == InviteStatus.ACTIVE

    user.is_registered = False
    service._has_oauth_account = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert await service.status_for(user) == InviteStatus.ACTIVE

    service._has_oauth_account = AsyncMock(return_value=False)  # type: ignore[method-assign]
    service._get_for_user = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert await service.status_for(user) == InviteStatus.NEVER_INVITED

    service._get_for_user = AsyncMock(  # type: ignore[method-assign]
        return_value=_invite(revoked_at=datetime.now(timezone.utc))
    )
    assert await service.status_for(user) == InviteStatus.NEVER_INVITED

    service._get_for_user = AsyncMock(  # type: ignore[method-assign]
        return_value=_invite(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )
    assert await service.status_for(user) == InviteStatus.EXPIRED

    service._get_for_user = AsyncMock(return_value=_invite())  # type: ignore[method-assign]
    assert await service.status_for(user) == InviteStatus.PENDING


@pytest.mark.asyncio
async def test_has_oauth_account_checks_for_any_account(session) -> None:
    session.execute.return_value = _ScalarResult(uuid4())

    assert await UserInviteService(session)._has_oauth_account(uuid4()) is True
