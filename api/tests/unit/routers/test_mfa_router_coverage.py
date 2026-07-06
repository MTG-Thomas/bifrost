from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.routers import mfa


def _db():
    return SimpleNamespace(commit=AsyncMock())


def _current_user(user_id=None):
    return SimpleNamespace(user_id=user_id or uuid4())


def _user(**overrides):
    data = {
        "id": uuid4(),
        "email": "user@example.test",
        "name": "Test User",
        "hashed_password": "hashed",
        "is_superuser": False,
        "organization_id": uuid4(),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_remove_mfa_requires_password_or_code():
    with pytest.raises(HTTPException) as exc:
        await mfa.remove_mfa(
            mfa.MFARemoveRequest(),
            _current_user(),
            _db(),
        )

    assert exc.value.status_code == 400
    assert "Either password or mfa_code" in exc.value.detail


@pytest.mark.asyncio
async def test_remove_mfa_password_paths(monkeypatch):
    db = _db()
    user = _user(hashed_password=None)
    service = SimpleNamespace(remove_totp=AsyncMock())
    monkeypatch.setattr(mfa, "get_current_user_from_db", AsyncMock(return_value=user))
    monkeypatch.setattr(mfa, "MFAService", lambda _db: service)

    with pytest.raises(HTTPException) as exc:
        await mfa.remove_mfa(
            mfa.MFARemoveRequest(password="secret"),
            _current_user(),
            db,
        )

    assert exc.value.status_code == 400
    assert "password authentication" in exc.value.detail

    user.hashed_password = "hashed"
    monkeypatch.setattr(mfa, "verify_password", lambda _password, _hashed: False)
    with pytest.raises(HTTPException) as exc:
        await mfa.remove_mfa(
            mfa.MFARemoveRequest(password="wrong"),
            _current_user(),
            db,
        )
    assert exc.value.status_code == 401

    monkeypatch.setattr(mfa, "verify_password", lambda _password, _hashed: True)
    result = await mfa.remove_mfa(
        mfa.MFARemoveRequest(password="secret"),
        _current_user(),
        db,
    )

    assert result == {"message": "MFA removed successfully"}
    service.remove_totp.assert_awaited_once_with(user)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_remove_mfa_mfa_code_path_denies_then_removes(monkeypatch):
    db = _db()
    user = _user()
    service = SimpleNamespace(
        verify_totp_code=AsyncMock(side_effect=[False, True]),
        remove_totp=AsyncMock(),
    )
    monkeypatch.setattr(mfa, "get_current_user_from_db", AsyncMock(return_value=user))
    monkeypatch.setattr(mfa, "MFAService", lambda _db: service)

    with pytest.raises(HTTPException) as exc:
        await mfa.remove_mfa(
            mfa.MFARemoveRequest(mfa_code="123456"),
            _current_user(),
            db,
        )
    assert exc.value.status_code == 401

    result = await mfa.remove_mfa(
        mfa.MFARemoveRequest(mfa_code="654321"),
        _current_user(),
        db,
    )

    assert result["message"] == "MFA removed successfully"
    assert service.verify_totp_code.await_count == 2
    service.remove_totp.assert_awaited_once_with(user)


@pytest.mark.asyncio
async def test_regenerate_recovery_codes_verifies_mfa_and_commits(monkeypatch):
    db = _db()
    user = _user()
    service = SimpleNamespace(
        verify_totp_code=AsyncMock(side_effect=[False, True]),
        regenerate_recovery_codes=AsyncMock(return_value=["one", "two"]),
    )
    monkeypatch.setattr(mfa, "get_current_user_from_db", AsyncMock(return_value=user))
    monkeypatch.setattr(mfa, "MFAService", lambda _db: service)

    with pytest.raises(HTTPException) as exc:
        await mfa.regenerate_recovery_codes(
            mfa.RegenerateRecoveryCodesRequest(mfa_code="111111"),
            _current_user(),
            db,
        )
    assert exc.value.status_code == 401

    result = await mfa.regenerate_recovery_codes(
        mfa.RegenerateRecoveryCodesRequest(mfa_code="222222"),
        _current_user(),
        db,
    )

    assert result.recovery_codes == ["one", "two"]
    assert result.count == 2
    service.regenerate_recovery_codes.assert_awaited_once_with(user.id)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_code_count_and_trusted_device_listing(monkeypatch):
    user_id = uuid4()
    now = datetime(2026, 7, 5, tzinfo=timezone.utc)
    current_fingerprint = "fingerprint-current"
    devices = [
        SimpleNamespace(
            id=uuid4(),
            device_name="Laptop",
            created_at=now,
            expires_at=now + timedelta(days=30),
            last_used_at=None,
            device_fingerprint=current_fingerprint,
        ),
        SimpleNamespace(
            id=uuid4(),
            device_name=None,
            created_at=now,
            expires_at=now + timedelta(days=30),
            last_used_at=now,
            device_fingerprint="other",
        ),
    ]
    service = SimpleNamespace(
        get_recovery_codes_count=AsyncMock(return_value={"total": 10, "remaining": 4}),
        get_trusted_devices=AsyncMock(return_value=devices),
    )

    class FakeMFAService:
        def __init__(self, _db):
            pass

        get_recovery_codes_count = service.get_recovery_codes_count
        get_trusted_devices = service.get_trusted_devices

        @staticmethod
        def generate_device_fingerprint(user_agent):
            assert user_agent == "UnitTest/1"
            return current_fingerprint

    monkeypatch.setattr(mfa, "MFAService", FakeMFAService)
    current = _current_user(user_id)

    counts = await mfa.get_recovery_codes_count(current, _db())
    trusted = await mfa.list_trusted_devices(
        SimpleNamespace(headers={"user-agent": "UnitTest/1"}),
        current,
        _db(),
    )

    assert counts.total == 10
    assert counts.remaining == 4
    assert len(trusted.devices) == 2
    assert trusted.devices[0].is_current is True
    assert trusted.devices[1].last_used_at == now.isoformat()


@pytest.mark.asyncio
async def test_revoke_trusted_devices_paths(monkeypatch):
    user_id = uuid4()
    device_id = UUID("11111111-1111-1111-1111-111111111111")
    db = _db()
    service = SimpleNamespace(
        revoke_trusted_device=AsyncMock(side_effect=[False, True]),
        revoke_all_trusted_devices=AsyncMock(return_value=3),
    )
    monkeypatch.setattr(mfa, "MFAService", lambda _db: service)
    current = _current_user(user_id)

    with pytest.raises(HTTPException) as exc:
        await mfa.revoke_trusted_device(device_id, current, db)
    assert exc.value.status_code == 404

    result = await mfa.revoke_trusted_device(device_id, current, db)
    assert result == {"message": "Device trust revoked"}

    all_result = await mfa.revoke_all_trusted_devices(current, db)
    assert all_result == {"message": "Revoked 3 trusted devices"}
    assert db.commit.await_count == 3
