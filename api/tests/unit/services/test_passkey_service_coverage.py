"""Additional behavior-focused unit tests for passkey service."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.constants import PROVIDER_ORG_ID
from src.models.orm.mfa import UserPasskey
from src.services.passkey_service import PasskeyService


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


async def test_generate_authentication_options_without_email_uses_discoverable_credentials(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = SimpleNamespace(challenge=b"discoverable-challenge")
    generate_options = MagicMock(return_value=options)
    monkeypatch.setattr(
        "src.services.passkey_service.generate_authentication_options",
        generate_options,
    )
    monkeypatch.setattr(
        "src.services.passkey_service.options_to_json",
        lambda value: json.dumps({"challenge": "serialized"}),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.bytes_to_base64url",
        lambda value: "auth-challenge-b64",
    )
    monkeypatch.setattr(
        "src.services.passkey_service.secrets.token_urlsafe",
        lambda size: "auth-token",
    )
    monkeypatch.setattr(
        "src.services.passkey_service.get_shared_redis",
        AsyncMock(return_value=redis),
    )

    challenge_id, payload = await service.generate_authentication_options()

    assert challenge_id == "auth-token"
    assert payload == {"challenge": "serialized"}
    assert generate_options.call_args.kwargs["allow_credentials"] is None
    assert generate_options.call_args.kwargs["rp_id"] == "bifrost.example.com"
    redis.setex.assert_awaited_once_with(
        "passkey_auth_challenge:auth-token",
        300,
        "auth-challenge-b64",
    )


async def test_generate_authentication_options_for_unknown_email_still_allows_resident_key_auth(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service._get_user_by_email = AsyncMock(return_value=None)
    generate_options = MagicMock(return_value=SimpleNamespace(challenge=b"challenge"))
    monkeypatch.setattr(
        "src.services.passkey_service.generate_authentication_options",
        generate_options,
    )
    monkeypatch.setattr(
        "src.services.passkey_service.options_to_json",
        lambda value: json.dumps({"challenge": "serialized"}),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.bytes_to_base64url",
        lambda value: "challenge-b64",
    )
    monkeypatch.setattr(
        "src.services.passkey_service.secrets.token_urlsafe",
        lambda size: "challenge-id",
    )
    monkeypatch.setattr(
        "src.services.passkey_service.get_shared_redis",
        AsyncMock(return_value=redis),
    )

    challenge_id, payload = await service.generate_authentication_options(
        "missing@example.com"
    )

    assert challenge_id == "challenge-id"
    assert payload == {"challenge": "serialized"}
    assert generate_options.call_args.kwargs["allow_credentials"] is None


async def test_verify_authentication_requires_single_use_challenge(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis.get.return_value = None
    monkeypatch.setattr(
        "src.services.passkey_service.get_shared_redis",
        AsyncMock(return_value=redis),
    )

    with pytest.raises(ValueError, match="Authentication challenge not found"):
        await service.verify_authentication("missing-token", "{}")

    redis.delete.assert_not_awaited()


async def test_verify_authentication_rejects_unknown_credential_after_deleting_challenge(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis.get.return_value = "challenge-b64"
    service._get_passkey_by_credential_id = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "src.services.passkey_service.get_shared_redis",
        AsyncMock(return_value=redis),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.base64url_to_bytes",
        lambda value: b"challenge",
    )
    monkeypatch.setattr(
        "src.services.passkey_service.parse_authentication_credential_json",
        lambda value: SimpleNamespace(raw_id=b"unknown-credential"),
    )

    with pytest.raises(ValueError, match="Unknown credential"):
        await service.verify_authentication("auth-token", "{}")

    redis.delete.assert_awaited_once_with("passkey_auth_challenge:auth-token")
    service._get_passkey_by_credential_id.assert_awaited_once_with(
        b"unknown-credential"
    )


async def test_verify_authentication_wraps_webauthn_verification_errors(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis.get.return_value = "challenge-b64"
    passkey = SimpleNamespace(
        user_id=uuid4(),
        public_key=b"public-key",
        sign_count=4,
    )
    service._get_passkey_by_credential_id = AsyncMock(return_value=passkey)
    monkeypatch.setattr(
        "src.services.passkey_service.get_shared_redis",
        AsyncMock(return_value=redis),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.base64url_to_bytes",
        lambda value: b"challenge",
    )
    monkeypatch.setattr(
        "src.services.passkey_service.parse_authentication_credential_json",
        lambda value: SimpleNamespace(raw_id=b"credential"),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.verify_authentication_response",
        MagicMock(side_effect=RuntimeError("bad signature")),
    )

    with pytest.raises(ValueError, match="Authentication verification failed"):
        await service.verify_authentication("auth-token", "{}")

    service.db.flush.assert_not_awaited()


async def test_list_passkeys_delegates_to_ordered_user_passkey_lookup(
    service: PasskeyService,
) -> None:
    user_id = uuid4()
    passkeys = [SimpleNamespace(name="Phone"), SimpleNamespace(name="Security Key")]
    service._get_user_passkeys = AsyncMock(return_value=passkeys)

    assert await service.list_passkeys(user_id) == passkeys
    service._get_user_passkeys.assert_awaited_once_with(user_id)


async def test_private_lookup_helpers_map_scalar_results(
    service: PasskeyService,
) -> None:
    user = SimpleNamespace(email="ada@example.com")
    passkey = SimpleNamespace(credential_id=b"credential")
    first_result = MagicMock()
    first_result.scalar_one_or_none.return_value = user
    second_result = MagicMock()
    second_result.scalar_one_or_none.return_value = passkey
    service.db.execute.side_effect = [first_result, second_result]

    assert await service._get_user_by_email("ada@example.com") is user
    assert await service._get_passkey_by_credential_id(b"credential") is passkey

    assert service.db.execute.await_count == 2


async def test_generate_setup_registration_options_rejects_existing_email(
    service: PasskeyService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_repo = AsyncMock()
    user_repo.has_any_users.return_value = False
    monkeypatch.setattr(
        "src.repositories.users.UserRepository",
        MagicMock(return_value=user_repo),
    )
    service._get_user_by_email = AsyncMock(return_value=SimpleNamespace())

    with pytest.raises(ValueError, match="Email already registered"):
        await service.generate_setup_registration_options("ada@example.com")


async def test_generate_setup_registration_options_stores_pending_admin_registration(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_repo = AsyncMock()
    user_repo.has_any_users.return_value = False
    service._get_user_by_email = AsyncMock(return_value=None)
    options = SimpleNamespace(challenge=b"setup-challenge")
    monkeypatch.setattr(
        "src.repositories.users.UserRepository",
        MagicMock(return_value=user_repo),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.generate_user_handle",
        lambda: b"temporary-user-handle",
    )
    monkeypatch.setattr(
        "src.services.passkey_service.generate_registration_options",
        MagicMock(return_value=options),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.options_to_json",
        lambda value: json.dumps({"challenge": "serialized"}),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.bytes_to_base64url",
        lambda value: f"b64:{value.decode()}",
    )
    monkeypatch.setattr(
        "src.services.passkey_service.secrets.token_urlsafe",
        lambda size: "registration-token",
    )
    monkeypatch.setattr(
        "src.services.passkey_service.get_shared_redis",
        AsyncMock(return_value=redis),
    )

    token, payload = await service.generate_setup_registration_options(
        "ada@example.com"
    )

    assert token == "registration-token"
    assert payload == {"challenge": "serialized"}
    key, ttl, stored_json = redis.setex.await_args.args
    assert key == "passkey_setup:registration-token"
    assert ttl == 300
    stored = json.loads(stored_json)
    assert stored == {
        "email": "ada@example.com",
        "name": "ada",
        "webauthn_user_id": "b64:temporary-user-handle",
        "challenge": "b64:setup-challenge",
    }


async def test_verify_setup_registration_wraps_registration_verification_errors(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis.get.return_value = json.dumps(
        {
            "email": "ada@example.com",
            "name": "Ada",
            "webauthn_user_id": "user-handle-b64",
            "challenge": "challenge-b64",
        }
    )
    monkeypatch.setattr(
        "src.services.passkey_service.get_shared_redis",
        AsyncMock(return_value=redis),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.base64url_to_bytes",
        lambda value: b"decoded",
    )
    monkeypatch.setattr(
        "src.services.passkey_service.parse_registration_credential_json",
        lambda value: SimpleNamespace(response=SimpleNamespace(transports=[])),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.verify_registration_response",
        MagicMock(side_effect=RuntimeError("bad attestation")),
    )

    with pytest.raises(ValueError, match="Registration verification failed"):
        await service.verify_setup_registration("registration-token", "{}")

    redis.delete.assert_awaited_once_with("passkey_setup:registration-token")
    service.db.add.assert_not_called()


async def test_verify_setup_registration_rejects_race_when_user_created_mid_flow(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_repo = AsyncMock()
    user_repo.has_any_users.return_value = True
    redis.get.return_value = json.dumps(
        {
            "email": "ada@example.com",
            "name": "Ada",
            "webauthn_user_id": "user-handle-b64",
            "challenge": "challenge-b64",
        }
    )
    monkeypatch.setattr(
        "src.repositories.users.UserRepository",
        MagicMock(return_value=user_repo),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.get_shared_redis",
        AsyncMock(return_value=redis),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.base64url_to_bytes",
        lambda value: b"decoded",
    )
    monkeypatch.setattr(
        "src.services.passkey_service.parse_registration_credential_json",
        lambda value: SimpleNamespace(response=SimpleNamespace(transports=[])),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.verify_registration_response",
        MagicMock(
            return_value=SimpleNamespace(
                credential_id=b"credential",
                credential_public_key=b"public-key",
                sign_count=1,
                credential_device_type="single_device",
                credential_backed_up=False,
            )
        ),
    )

    with pytest.raises(ValueError, match="Another user was created"):
        await service.verify_setup_registration("registration-token", "{}")

    service.db.add.assert_not_called()


async def test_verify_setup_registration_creates_platform_admin_and_setup_passkey(
    service: PasskeyService,
    redis: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_repo = AsyncMock()
    user_repo.has_any_users.return_value = False
    redis.get.return_value = json.dumps(
        {
            "email": "ada@example.com",
            "name": "Ada Lovelace",
            "webauthn_user_id": "user-handle-b64",
            "challenge": "challenge-b64",
        }
    )
    credential = SimpleNamespace(
        response=SimpleNamespace(transports=["internal", "hybrid"])
    )
    verification = SimpleNamespace(
        credential_id=b"credential",
        credential_public_key=b"public-key",
        sign_count=11,
        credential_device_type="multi_device",
        credential_backed_up=True,
    )
    monkeypatch.setattr(
        "src.repositories.users.UserRepository",
        MagicMock(return_value=user_repo),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.get_shared_redis",
        AsyncMock(return_value=redis),
    )
    monkeypatch.setattr(
        "src.services.passkey_service.base64url_to_bytes",
        lambda value: b"decoded",
    )
    monkeypatch.setattr(
        "src.services.passkey_service.parse_registration_credential_json",
        lambda value: credential,
    )
    monkeypatch.setattr(
        "src.services.passkey_service.verify_registration_response",
        MagicMock(return_value=verification),
    )

    user, passkey = await service.verify_setup_registration("registration-token", "{}")

    assert user.email == "ada@example.com"
    assert user.name == "Ada Lovelace"
    assert user.is_active is True
    assert user.is_superuser is True
    assert user.is_registered is True
    assert user.hashed_password is None
    assert user.webauthn_user_id == b"decoded"
    assert user.organization_id == PROVIDER_ORG_ID
    assert isinstance(passkey, UserPasskey)
    assert passkey.credential_id == b"credential"
    assert passkey.public_key == b"public-key"
    assert passkey.sign_count == 11
    assert passkey.transports == ["internal", "hybrid"]
    assert passkey.name == "Setup Passkey"
    assert service.db.add.call_args_list[0].args == (user,)
    assert service.db.add.call_args_list[1].args == (passkey,)
    assert service.db.flush.await_count == 2
