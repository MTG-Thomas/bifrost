from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.enums import MFAMethodType
from src.services import mfa_service
from src.services.mfa_service import MFAService


class FakeDb:
    def __init__(self, execute_results: list[Any] | None = None) -> None:
        self.execute_results = execute_results or []
        self.added: list[Any] = []
        self.flushes = 0
        self.executed: list[Any] = []

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1

    async def execute(self, statement: Any) -> Any:
        self.executed.append(statement)
        return self.execute_results.pop(0)


class ScalarResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar(self) -> Any:
        return self.value

    def scalar_one_or_none(self) -> Any:
        return self.value


class RowCountResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class ScalarListResult:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def scalars(self) -> "ScalarListResult":
        return self

    def all(self) -> list[Any]:
        return self.values


def _service(db: FakeDb | None = None) -> MFAService:
    return MFAService(db or FakeDb())  # type: ignore[arg-type]


def _async_return(value: Any):
    async def inner(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return inner


class _FakeTotp:
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def provisioning_uri(self, name: str, issuer_name: str) -> str:
        return f"otpauth://totp/{issuer_name}:{name}?secret={self.secret}"


@pytest.mark.asyncio
async def test_get_mfa_status_aggregates_methods_and_recovery_count(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    user_id = uuid4()
    user = SimpleNamespace(id=user_id, mfa_enabled=True, mfa_enforced_at="deadline")
    methods = [SimpleNamespace(method_type=MFAMethodType.TOTP)]
    monkeypatch.setattr(service, "_get_active_methods", _async_return(methods))
    monkeypatch.setattr(service, "_count_unused_recovery_codes", _async_return(4))

    status = await service.get_mfa_status(user)  # type: ignore[arg-type]

    assert status == {
        "mfa_enabled": True,
        "mfa_required": True,
        "enforcement_deadline": "deadline",
        "enrolled_methods": ["totp"],
        "recovery_codes_remaining": 4,
    }


def test_generate_device_fingerprint_is_deterministic_and_uses_additional_data() -> None:
    first = MFAService.generate_device_fingerprint("ua", "screen=1")
    second = MFAService.generate_device_fingerprint("ua", "screen=1")
    different = MFAService.generate_device_fingerprint("ua", "screen=2")

    assert first == second
    assert first != different
    assert len(first) == 64


@pytest.mark.asyncio
async def test_setup_totp_reuses_recent_pending_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    user = SimpleNamespace(id=uuid4(), email="operator@example.test")
    pending = SimpleNamespace(
        encrypted_secret="encrypted-secret",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    monkeypatch.setattr(service, "_get_pending_totp", _async_return(pending))
    monkeypatch.setattr(mfa_service, "decrypt_secret", lambda encrypted: "decoded-secret")
    monkeypatch.setattr(mfa_service.pyotp, "TOTP", _FakeTotp)

    result = await service.setup_totp(user)  # type: ignore[arg-type]

    assert result["is_existing"] is True
    assert result["secret"] == "decoded-secret"
    assert result["account_name"] == "operator@example.test"
    assert result["issuer"] == service.settings.mfa_totp_issuer
    assert result["provisioning_uri"].startswith("otpauth://totp/")


@pytest.mark.asyncio
async def test_verify_recovery_code_marks_matching_code_used(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDb()
    service = _service(db)
    user_id = uuid4()
    matching = SimpleNamespace(code_hash="hash-ok", is_used=False, used_at=None, used_from_ip=None)
    other = SimpleNamespace(code_hash="hash-no", is_used=False)
    monkeypatch.setattr(service, "_get_unused_recovery_codes", _async_return([other, matching]))
    monkeypatch.setattr(mfa_service, "verify_password", lambda normalized, code_hash: code_hash == "hash-ok")

    assert await service.verify_recovery_code(user_id, "abcd-1234", ip_address="203.0.113.5") is True

    assert matching.is_used is True
    assert matching.used_at is not None
    assert matching.used_from_ip == "203.0.113.5"
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_verify_recovery_code_returns_false_without_match(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDb()
    service = _service(db)
    monkeypatch.setattr(service, "_get_unused_recovery_codes", _async_return([SimpleNamespace(code_hash="hash")]))
    monkeypatch.setattr(mfa_service, "verify_password", lambda _normalized, _code_hash: False)

    assert await service.verify_recovery_code(uuid4(), "bad-code") is False
    assert db.flushes == 0


@pytest.mark.asyncio
async def test_verify_totp_enrollment_requires_pending_method(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    monkeypatch.setattr(service, "_get_pending_totp", _async_return(None))

    with pytest.raises(ValueError, match="No pending TOTP enrollment found"):
        await service.verify_totp_enrollment(SimpleNamespace(id=uuid4()), "123456")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_verify_totp_enrollment_requires_pending_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    monkeypatch.setattr(service, "_get_pending_totp", _async_return(SimpleNamespace(encrypted_secret=None)))

    with pytest.raises(ValueError, match="MFA method has no encrypted secret"):
        await service.verify_totp_enrollment(SimpleNamespace(id=uuid4()), "123456")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_verify_totp_enrollment_rejects_invalid_code(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    monkeypatch.setattr(
        service,
        "_get_pending_totp",
        _async_return(SimpleNamespace(encrypted_secret="encrypted")),
    )
    monkeypatch.setattr(mfa_service, "decrypt_secret", lambda encrypted: "decoded-secret")

    class RejectingTotp(_FakeTotp):
        def verify(self, code: str, valid_window: int) -> bool:
            assert code == "111111"
            assert valid_window == service.settings.mfa_totp_enrollment_window
            return False

    monkeypatch.setattr(mfa_service.pyotp, "TOTP", RejectingTotp)

    with pytest.raises(ValueError, match="Invalid TOTP code"):
        await service.verify_totp_enrollment(SimpleNamespace(id=uuid4()), "111111")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_verify_totp_code_requires_active_method(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    monkeypatch.setattr(service, "_get_active_totp", _async_return(None))

    with pytest.raises(ValueError, match="TOTP not configured"):
        await service.verify_totp_code(uuid4(), "123456")


@pytest.mark.asyncio
async def test_verify_totp_code_requires_configured_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    monkeypatch.setattr(service, "_get_active_totp", _async_return(SimpleNamespace(encrypted_secret=None)))

    with pytest.raises(ValueError, match="MFA method has no encrypted secret"):
        await service.verify_totp_code(uuid4(), "123456")


@pytest.mark.asyncio
async def test_remove_totp_deletes_methods_codes_and_disables_last_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDb([RowCountResult(1)])
    service = _service(db)
    user = SimpleNamespace(id=uuid4(), mfa_enabled=True)
    monkeypatch.setattr(service, "_delete_recovery_codes", AsyncMock())
    monkeypatch.setattr(service, "_count_active_methods", _async_return(0))

    await service.remove_totp(user)  # type: ignore[arg-type]

    assert len(db.executed) == 1
    service._delete_recovery_codes.assert_awaited_once_with(user.id)  # type: ignore[attr-defined]
    assert user.mfa_enabled is False
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_get_recovery_codes_count_returns_total_and_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    user_id = uuid4()
    monkeypatch.setattr(service, "_count_recovery_codes", _async_return(8))
    monkeypatch.setattr(service, "_count_unused_recovery_codes", _async_return(3))

    assert await service.get_recovery_codes_count(user_id) == {
        "total": 8,
        "remaining": 3,
    }


@pytest.mark.asyncio
async def test_verify_totp_code_returns_false_for_invalid_code(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDb()
    service = _service(db)
    method = SimpleNamespace(encrypted_secret="encrypted", last_used_at=None)
    monkeypatch.setattr(service, "_get_active_totp", _async_return(method))
    monkeypatch.setattr(mfa_service, "decrypt_secret", lambda encrypted: "secret")

    class FakeTotp:
        def __init__(self, secret: str) -> None:
            self.secret = secret

        def verify(self, code: str, valid_window: int) -> bool:
            assert code == "000000"
            assert valid_window == service.settings.mfa_totp_login_window
            return False

    monkeypatch.setattr(mfa_service.pyotp, "TOTP", FakeTotp)

    assert await service.verify_totp_code(uuid4(), "000000") is False
    assert method.last_used_at is None
    assert db.flushes == 0


@pytest.mark.asyncio
async def test_create_trusted_device_updates_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDb()
    service = _service(db)
    existing = SimpleNamespace(expires_at=None, last_used_at=None, last_ip_address=None)
    monkeypatch.setattr(service, "_get_trusted_device", _async_return(existing))

    result = await service.create_trusted_device(uuid4(), "fp", ip_address="203.0.113.10")

    assert result is existing
    assert existing.expires_at > datetime.now(timezone.utc)
    assert existing.last_used_at is not None
    assert existing.last_ip_address == "203.0.113.10"
    assert db.added == []
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_create_trusted_device_adds_new_device(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDb()
    service = _service(db)
    user_id = uuid4()
    monkeypatch.setattr(service, "_get_trusted_device", _async_return(None))

    device = await service.create_trusted_device(user_id, "fp", device_name="Laptop", ip_address="203.0.113.10")

    assert device.user_id == user_id
    assert device.device_fingerprint == "fp"
    assert device.device_name == "Laptop"
    assert device.last_ip_address == "203.0.113.10"
    assert db.added == [device]
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_is_device_trusted_handles_missing_expired_and_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDb()
    service = _service(db)
    user_id = uuid4()

    monkeypatch.setattr(service, "_get_trusted_device", _async_return(None))
    assert await service.is_device_trusted(user_id, "fp") is False

    expired = SimpleNamespace(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    monkeypatch.setattr(service, "_get_trusted_device", _async_return(expired))
    assert await service.is_device_trusted(user_id, "fp") is False

    valid = SimpleNamespace(
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        last_used_at=None,
        last_ip_address=None,
    )
    monkeypatch.setattr(service, "_get_trusted_device", _async_return(valid))
    assert await service.is_device_trusted(user_id, "fp", ip_address="203.0.113.20") is True
    assert valid.last_used_at is not None
    assert valid.last_ip_address == "203.0.113.20"
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_revoke_trusted_device_and_all_return_rowcounts() -> None:
    db = FakeDb([RowCountResult(1), RowCountResult(3)])
    service = _service(db)

    assert await service.revoke_trusted_device(uuid4(), uuid4()) is True
    assert await service.revoke_all_trusted_devices(uuid4()) == 3
    assert db.flushes == 2


@pytest.mark.asyncio
async def test_get_trusted_devices_returns_ordered_scalars_from_query() -> None:
    devices = [SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())]
    db = FakeDb([ScalarListResult(devices)])
    service = _service(db)

    assert await service.get_trusted_devices(uuid4()) == devices
    assert len(db.executed) == 1


@pytest.mark.asyncio
async def test_count_helpers_default_missing_scalar_to_zero() -> None:
    db = FakeDb([ScalarResult(None), ScalarResult(2), ScalarResult(5)])
    service = _service(db)
    user_id = uuid4()

    assert await service._count_active_methods(user_id) == 0
    assert await service._count_recovery_codes(user_id) == 2
    assert await service._count_unused_recovery_codes(user_id) == 5
