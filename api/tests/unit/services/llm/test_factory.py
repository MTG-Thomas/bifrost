from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.llm import factory
from src.services.llm.base import LLMConfig


@pytest.mark.asyncio
async def test_get_llm_config_resolves_requested_profile(monkeypatch) -> None:
    profile_id = uuid4()
    config = LLMConfig(provider="openai", model="gpt-test", api_key="key")
    resolve = AsyncMock(return_value=config)
    monkeypatch.setattr(
        factory,
        "AIModelService",
        lambda session: type("Service", (), {"resolve_config": resolve})(),
    )
    session = object()

    result = await factory.get_llm_config(
        session,  # type: ignore[arg-type]
        profile_id=profile_id,
        profile_name="ignored-when-service-validates",
        assignment_key="summarizer",
    )

    assert result is config
    resolve.assert_awaited_once_with(
        profile_id=profile_id,
        profile_name="ignored-when-service-validates",
        assignment_key="summarizer",
    )


@pytest.mark.asyncio
async def test_get_llm_config_preserves_validation_errors(monkeypatch) -> None:
    resolve = AsyncMock(side_effect=ValueError("profile not configured"))
    monkeypatch.setattr(
        factory,
        "AIModelService",
        lambda _session: type("Service", (), {"resolve_config": resolve})(),
    )

    with pytest.raises(ValueError, match="profile not configured"):
        await factory.get_llm_config(object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_llm_config_hides_internal_resolution_errors(monkeypatch) -> None:
    resolve = AsyncMock(side_effect=RuntimeError("database details"))
    monkeypatch.setattr(
        factory,
        "AIModelService",
        lambda _session: type("Service", (), {"resolve_config": resolve})(),
    )

    with pytest.raises(ValueError, match="Failed to resolve LLM configuration"):
        await factory.get_llm_config(object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_llm_client_uses_provider_neutral_client(monkeypatch) -> None:
    config = LLMConfig(provider="anthropic", model="claude-test", api_key="key")
    monkeypatch.setattr(factory, "get_llm_config", AsyncMock(return_value=config))

    class Client:
        def __init__(self, resolved: LLMConfig) -> None:
            self.config = resolved

    monkeypatch.setattr("src.services.llm.pydantic_client.PydanticAIClient", Client)

    client = await factory.get_llm_client(object())  # type: ignore[arg-type]

    assert isinstance(client, Client)
    assert client.config is config


def test_create_llm_client_uses_default_model(monkeypatch) -> None:
    class Client:
        def __init__(self, config: LLMConfig) -> None:
            self.config = config

    monkeypatch.setattr("src.services.llm.pydantic_client.PydanticAIClient", Client)

    client = factory.create_llm_client("google", api_key="key")

    assert isinstance(client, Client)
    assert client.config.model == factory.DEFAULT_GOOGLE_MODEL


def test_create_llm_client_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unknown LLM provider: bogus"):
        factory.create_llm_client("bogus", api_key="key")  # type: ignore[arg-type]
