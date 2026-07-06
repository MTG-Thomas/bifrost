from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from src.services.llm import factory
from src.services.llm.base import LLMConfig


class _Scalars:
    def __init__(self, config):
        self._config = config

    def first(self):
        return self._config


class _Result:
    def __init__(self, config):
        self._config = config

    def scalars(self):
        return _Scalars(self._config)


class _Session:
    def __init__(self, config):
        self.config = config
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self.config)


def _encrypt(secret_key: str, value: str) -> str:
    key_bytes = secret_key.encode()[:32].ljust(32, b"0")
    return Fernet(base64.urlsafe_b64encode(key_bytes)).encrypt(value.encode()).decode()


@pytest.mark.asyncio
async def test_get_llm_config_rejects_missing_provider_config(monkeypatch):
    monkeypatch.setattr(factory, "get_settings", lambda: SimpleNamespace(secret_key="secret"))

    with pytest.raises(ValueError, match="LLM provider not configured"):
        await factory.get_llm_config(_Session(None))

    with pytest.raises(ValueError, match="LLM provider not configured"):
        await factory.get_llm_config(_Session(SimpleNamespace(value_json={})))


@pytest.mark.asyncio
async def test_get_llm_config_maps_custom_provider_and_decrypts_key(monkeypatch):
    secret = "super-secret"
    monkeypatch.setattr(factory, "get_settings", lambda: SimpleNamespace(secret_key=secret))
    session = _Session(
        SimpleNamespace(
            value_json={
                "provider": "custom",
                "model": "custom-model",
                "encrypted_api_key": _encrypt(secret, "api-key"),
                "endpoint": "https://llm.example.test",
                "max_tokens": 99,
            }
        )
    )

    config = await factory.get_llm_config(session)

    assert config == LLMConfig(
        provider="openai",
        model="custom-model",
        api_key="api-key",
        endpoint="https://llm.example.test",
        max_tokens=99,
    )
    assert session.statements


@pytest.mark.asyncio
async def test_get_llm_config_defaults_invalid_provider_and_requires_api_key(
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(factory, "get_settings", lambda: SimpleNamespace(secret_key="secret"))
    session = _Session(SimpleNamespace(value_json={"provider": "bogus"}))

    with pytest.raises(ValueError, match="No API key configured"):
        await factory.get_llm_config(session)

    assert "Invalid provider 'bogus', defaulting to openai" in caplog.text


@pytest.mark.asyncio
async def test_get_llm_config_reports_corrupted_encrypted_api_key(monkeypatch):
    monkeypatch.setattr(factory, "get_settings", lambda: SimpleNamespace(secret_key="secret"))
    session = _Session(
        SimpleNamespace(
            value_json={
                "provider": "anthropic",
                "encrypted_api_key": "not-fernet",
            }
        )
    )

    with pytest.raises(ValueError, match="Failed to decrypt LLM API key"):
        await factory.get_llm_config(session)


@pytest.mark.asyncio
async def test_get_llm_client_selects_provider_specific_client(monkeypatch):
    class OpenAIClient:
        def __init__(self, config):
            self.config = config

    class AnthropicClient:
        def __init__(self, config):
            self.config = config

    monkeypatch.setattr(
        "src.services.llm.openai_client.OpenAIClient",
        OpenAIClient,
    )
    monkeypatch.setattr(
        "src.services.llm.anthropic_client.AnthropicClient",
        AnthropicClient,
    )

    async def openai_config(session):
        return LLMConfig(
            provider="openai",
            model="gpt-test",
            api_key="key",
        )

    monkeypatch.setattr(
        factory,
        "get_llm_config",
        openai_config,
    )

    openai_client = await factory.get_llm_client(object())
    assert isinstance(openai_client, OpenAIClient)

    async def anthropic_config(session):
        return LLMConfig(
            provider="anthropic",
            model="claude-test",
            api_key="key",
        )

    monkeypatch.setattr(
        factory,
        "get_llm_config",
        anthropic_config,
    )

    anthropic_client = await factory.get_llm_client(object())
    assert isinstance(anthropic_client, AnthropicClient)


def test_create_llm_client_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unknown LLM provider: bogus"):
        factory.create_llm_client("bogus", api_key="key")  # type: ignore[arg-type]
