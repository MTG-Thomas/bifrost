"""Unit tests for passkey service helpers."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from webauthn.helpers.structs import AuthenticatorTransport  # type: ignore[reportMissingImports]
from webauthn import generate_registration_options, options_to_json  # type: ignore[reportMissingImports]
from webauthn.helpers.structs import PublicKeyCredentialDescriptor  # type: ignore[reportMissingImports]

from src.services.passkey_service import PasskeyService, _normalize_transports


@pytest.fixture
def db_session() -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    return session


@pytest.fixture
def service(db_session: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> PasskeyService:
    settings = SimpleNamespace(
        webauthn_rp_id="bifrost.example.com",
        webauthn_rp_name="Bifrost",
        webauthn_origins=["https://bifrost.example.com"],
    )
    monkeypatch.setattr("src.services.passkey_service.get_settings", lambda: settings)
    return PasskeyService(db_session)


@pytest.fixture
def redis() -> AsyncMock:
    client = AsyncMock()
    client.get = AsyncMock()
    client.setex = AsyncMock()
    client.delete = AsyncMock()
    return client


def test_normalize_transports_converts_stored_strings_to_webauthn_enums():
    """Stored JSONB strings should be safe to pass back to py_webauthn."""
    transports = _normalize_transports(["internal", "hybrid"])

    assert transports == [
        AuthenticatorTransport.INTERNAL,
        AuthenticatorTransport.HYBRID,
    ]


def test_normalize_transports_ignores_unknown_or_empty_values():
    """Unexpected stored transport values should not break passkey registration."""
    transports = _normalize_transports(["internal", "", None, "future-transport"])

    assert transports == [AuthenticatorTransport.INTERNAL]


def test_normalized_transports_are_safe_for_registration_options_serialization():
    """Regression for py_webauthn expecting descriptor transports as enums."""
    options = generate_registration_options(
        rp_id="bifrost.example.com",
        rp_name="Bifrost",
        user_id=b"test-user-handle",
        user_name="user@example.com",
        user_display_name="User",
        exclude_credentials=[
            PublicKeyCredentialDescriptor(
                id=b"existing-credential-id",
                transports=_normalize_transports(["internal"]),
            )
        ],
    )

    serialized = options_to_json(options)

    assert '"transports": ["internal"]' in serialized


async def test_generate_registration_options_requires_existing_user(
    service: PasskeyService,
) -> None:
    service._get_user_by_id = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="User not found"):
        await service.generate_registration_options(uuid4())


async def test_generate_registration_options_creates_handle_and_stores_challenge(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        email="ada@example.com",
        name=None,
        webauthn_user_id=None,
    )
    options = SimpleNamespace(challenge=b"challenge")
    service._get_user_by_id = AsyncMock(return_value=user)
    service._get_user_passkeys = AsyncMock(
        return_value=[
            SimpleNamespace(
                credential_id=b"existing",
                transports=["internal", "unknown"],
            )
        ]
    )
    monkeypatch.setattr(
        "src.services.passkey_service.generate_user_handle", lambda: b"user-handle"
    )
    generate_options = MagicMock(return_value=options)
    monkeypatch.setattr(
        "src.services.passkey_service.generate_registration_options",
        generate_options,
    )
    monkeypatch.setattr(
        "src.services.passkey_service.options_to_json",
        lambda value: json.dumps({"challenge": "serialized"}),
    )
    monkeypatch.setattr("src.services.passkey_service.get_shared_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr(
        "src.services.passkey_service.bytes_to_base64url", lambda value: "challenge-b64"
    )

    result = await service.generate_registration_options(user_id)

    assert result == {"challenge": "serialized"}
    assert user.webauthn_user_id == b"user-handle"
    service.db.flush.assert_awaited_once()
    redis.setex.assert_awaited_once()
    assert redis.setex.await_args.args[0] == f"passkey_reg_challenge:{user_id}"
    assert generate_options.call_args.kwargs["user_display_name"] == "ada@example.com"
    excluded = generate_options.call_args.kwargs["exclude_credentials"]
    assert excluded[0].id == b"existing"
    assert excluded[0].transports == [AuthenticatorTransport.INTERNAL]


async def test_verify_registration_requires_user(service: PasskeyService) -> None:
    service._get_user_by_id = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="User not found"):
        await service.verify_registration(uuid4(), "{}")


async def test_verify_registration_requires_challenge(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    service._get_user_by_id = AsyncMock(return_value=SimpleNamespace(id=user_id))
    redis.get.return_value = None
    monkeypatch.setattr("src.services.passkey_service.get_shared_redis", AsyncMock(return_value=redis))

    with pytest.raises(ValueError, match="Registration challenge not found"):
        await service.verify_registration(user_id, "{}")


async def test_verify_registration_stores_passkey(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    user = SimpleNamespace(id=user_id)
    credential = SimpleNamespace(
        response=SimpleNamespace(transports=["internal", "hybrid"])
    )
    verification = SimpleNamespace(
        credential_id=b"credential",
        credential_public_key=b"public-key",
        sign_count=7,
        credential_device_type="single_device",
        credential_backed_up=False,
    )
    service._get_user_by_id = AsyncMock(return_value=user)
    redis.get.return_value = "challenge-b64"
    monkeypatch.setattr("src.services.passkey_service.get_shared_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr("src.services.passkey_service.base64url_to_bytes", lambda value: b"challenge")
    monkeypatch.setattr(
        "src.services.passkey_service.parse_registration_credential_json",
        lambda value: credential,
    )
    monkeypatch.setattr(
        "src.services.passkey_service.verify_registration_response",
        MagicMock(return_value=verification),
    )

    passkey = await service.verify_registration(user_id, "{}", device_name="Laptop")

    assert passkey.user_id == user_id
    assert passkey.credential_id == b"credential"
    assert passkey.public_key == b"public-key"
    assert passkey.sign_count == 7
    assert passkey.transports == ["internal", "hybrid"]
    assert passkey.name == "Laptop"
    service.db.add.assert_called_once_with(passkey)
    service.db.flush.assert_awaited_once()
    redis.delete.assert_awaited_once_with(f"passkey_reg_challenge:{user_id}")


async def test_generate_authentication_options_targets_known_user_passkeys(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    service._get_user_by_email = AsyncMock(return_value=SimpleNamespace(id=user_id))
    service._get_user_passkeys = AsyncMock(
        return_value=[
            SimpleNamespace(credential_id=b"credential", transports=["internal"])
        ]
    )
    options = SimpleNamespace(challenge=b"auth-challenge")
    generate_options = MagicMock(return_value=options)
    monkeypatch.setattr(
        "src.services.passkey_service.generate_authentication_options",
        generate_options,
    )
    monkeypatch.setattr(
        "src.services.passkey_service.options_to_json",
        lambda value: json.dumps({"allowCredentials": ["credential"]}),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.bytes_to_base64url", lambda value: "auth-b64"
    )
    monkeypatch.setattr(
        "src.services.passkey_service.secrets.token_urlsafe",
        lambda size: "challenge-id",
    )
    monkeypatch.setattr("src.services.passkey_service.get_shared_redis", AsyncMock(return_value=redis))

    challenge_id, payload = await service.generate_authentication_options(
        "ada@example.com"
    )

    assert challenge_id == "challenge-id"
    assert payload == {"allowCredentials": ["credential"]}
    assert generate_options.call_args.kwargs["allow_credentials"][0].id == b"credential"
    redis.setex.assert_awaited_once()
    assert redis.setex.await_args.args[0] == "passkey_auth_challenge:challenge-id"


async def test_verify_authentication_updates_passkey_and_returns_active_user(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    credential = SimpleNamespace(raw_id=b"credential")
    passkey = SimpleNamespace(
        user_id=user_id,
        public_key=b"public-key",
        sign_count=1,
        last_used_at=None,
    )
    user = SimpleNamespace(id=user_id, is_active=True)
    redis.get.return_value = "challenge-b64"
    service._get_passkey_by_credential_id = AsyncMock(return_value=passkey)
    service._get_user_by_id = AsyncMock(return_value=user)
    monkeypatch.setattr("src.services.passkey_service.get_shared_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr("src.services.passkey_service.base64url_to_bytes", lambda value: b"challenge")
    monkeypatch.setattr(
        "src.services.passkey_service.parse_authentication_credential_json",
        lambda value: credential,
    )
    monkeypatch.setattr(
        "src.services.passkey_service.verify_authentication_response",
        MagicMock(return_value=SimpleNamespace(new_sign_count=2)),
    )

    assert await service.verify_authentication("challenge-id", "{}") is user
    assert passkey.sign_count == 2
    assert passkey.last_used_at is not None
    service.db.flush.assert_awaited_once()


async def test_verify_authentication_errors_for_unknown_or_inactive_user(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis.get.return_value = "challenge-b64"
    service._get_passkey_by_credential_id = AsyncMock(
        return_value=SimpleNamespace(
            user_id=uuid4(),
            public_key=b"public-key",
            sign_count=1,
        )
    )
    monkeypatch.setattr("src.services.passkey_service.get_shared_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr("src.services.passkey_service.base64url_to_bytes", lambda value: b"challenge")
    monkeypatch.setattr(
        "src.services.passkey_service.parse_authentication_credential_json",
        lambda value: SimpleNamespace(raw_id=b"credential"),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.verify_authentication_response",
        MagicMock(return_value=SimpleNamespace(new_sign_count=2)),
    )

    service._get_user_by_id = AsyncMock(return_value=None)
    with pytest.raises(ValueError, match="User not found"):
        await service.verify_authentication("challenge-id", "{}")

    service._get_user_by_id = AsyncMock(return_value=SimpleNamespace(is_active=False))
    with pytest.raises(ValueError, match="inactive"):
        await service.verify_authentication("challenge-id", "{}")


async def test_delete_passkey_returns_false_when_missing(
    service: PasskeyService,
) -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    service.db.execute.return_value = result

    assert await service.delete_passkey(uuid4(), uuid4()) is False


async def test_delete_passkey_rejects_other_user(service: PasskeyService) -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = SimpleNamespace(user_id=uuid4())
    service.db.execute.return_value = result

    with pytest.raises(ValueError, match="does not belong"):
        await service.delete_passkey(uuid4(), uuid4())


async def test_delete_passkey_deletes_owned_passkey(service: PasskeyService) -> None:
    user_id = uuid4()
    passkey = SimpleNamespace(user_id=user_id)
    result = MagicMock()
    result.scalar_one_or_none.return_value = passkey
    service.db.execute.return_value = result

    assert await service.delete_passkey(user_id, uuid4()) is True
    service.db.delete.assert_awaited_once_with(passkey)
    service.db.flush.assert_awaited_once()


async def test_get_passkey_count_counts_scalars(service: PasskeyService) -> None:
    result = MagicMock()
    result.scalars.return_value.all.return_value = [object(), object()]
    service.db.execute.return_value = result

    assert await service.get_passkey_count(uuid4()) == 2


async def test_generate_setup_registration_options_rejects_existing_users(
    service: PasskeyService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_repo = AsyncMock()
    user_repo.has_any_users.return_value = True
    monkeypatch.setattr(
        "src.repositories.users.UserRepository", MagicMock(return_value=user_repo)
    )

    with pytest.raises(ValueError, match="first-time platform setup"):
        await service.generate_setup_registration_options("ada@example.com")


async def test_verify_setup_registration_requires_token(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis.get.return_value = None
    monkeypatch.setattr("src.services.passkey_service.get_shared_redis", AsyncMock(return_value=redis))

    with pytest.raises(ValueError, match="Registration token not found"):
        await service.verify_setup_registration("token", "{}")
