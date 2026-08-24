from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services import model_registry


class FakeRedis:
    def __init__(self, cached: str | None = None, fail: bool = False) -> None:
        self.cached = cached
        self.fail = fail
        self.setex_calls: list[tuple[str, int, str]] = []
        self.deleted: list[str] = []

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise RuntimeError("redis down")
        return self.cached

    async def setex(self, key: str, ttl: int, value: str) -> None:
        if self.fail:
            raise RuntimeError("redis down")
        self.setex_calls.append((key, ttl, value))

    async def delete(self, key: str) -> None:
        if self.fail:
            raise RuntimeError("redis down")
        self.deleted.append(key)


@pytest.mark.asyncio
async def test_google_model_mapping_uses_native_model_list() -> None:
    model = MagicMock()
    model.name = "models/gemini-2.5-flash"
    model.display_name = "Gemini 2.5 Flash"
    google_client = MagicMock()
    google_client.aio.models.list = AsyncMock(return_value=MagicMock(page=[model]))
    google_client.aio.aclose = AsyncMock()

    with patch("google.genai.Client", return_value=google_client):
        mapping = await model_registry._fetch_model_mapping("google", "test-key")

    assert mapping == {"gemini-2.5-flash": "Gemini 2.5 Flash"}
    google_client.aio.aclose.assert_awaited_once()


def test_normalize_model_name_strips_provider_date_suffixes() -> None:
    assert model_registry.normalize_model_name("claude-opus-4-5-20251101") == "claude-opus-4-5"
    assert model_registry.normalize_model_name("gpt-4o-2024-11-20") == "gpt-4o"
    assert model_registry.normalize_model_name("custom-model") == "custom-model"


@pytest.mark.asyncio
async def test_get_display_name_returns_cached_mapping() -> None:
    redis = FakeRedis(json.dumps({"claude-opus-4-5-20251101": "Claude Opus 4.5"}))

    assert (
        await model_registry.get_display_name(
            redis,
            "anthropic",
            "claude-opus-4-5-20251101",
        )
        == "Claude Opus 4.5"
    )


@pytest.mark.asyncio
async def test_get_display_name_refreshes_cache_on_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refresh(redis_client, provider, api_key):
        assert provider == "openai"
        assert api_key == "key"
        return {"gpt-4o-2024-11-20": "gpt-4o"}

    monkeypatch.setattr(model_registry, "refresh_model_registry", refresh)

    assert (
        await model_registry.get_display_name(
            FakeRedis("{}"),
            "openai",
            "gpt-4o-2024-11-20",
            api_key="key",
        )
        == "gpt-4o"
    )


@pytest.mark.asyncio
async def test_get_display_name_falls_back_when_cache_and_refresh_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refresh(redis_client, provider, api_key):
        raise RuntimeError("provider down")

    monkeypatch.setattr(model_registry, "refresh_model_registry", refresh)

    assert (
        await model_registry.get_display_name(
            FakeRedis(fail=True),
            "openai",
            "gpt-4o-2024-11-20",
            api_key="key",
        )
        == "gpt-4o"
    )


@pytest.mark.asyncio
async def test_cache_model_mapping_skips_empty_mapping_and_writes_json() -> None:
    redis = FakeRedis()

    await model_registry.cache_model_mapping(redis, "openai", {})
    assert redis.setex_calls == []

    await model_registry.cache_model_mapping(redis, "openai", {"a": "A"})

    assert redis.setex_calls == [
        (
            "model_registry:openai",
            model_registry.MODEL_REGISTRY_TTL,
            json.dumps({"a": "A"}),
        )
    ]


@pytest.mark.asyncio
async def test_refresh_model_registry_fetches_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fetch(provider, api_key):
        assert provider == "anthropic"
        assert api_key == "key"
        return {"claude": "Claude"}

    redis = FakeRedis()
    monkeypatch.setattr(model_registry, "_fetch_model_mapping", fetch)

    assert await model_registry.refresh_model_registry(redis, "anthropic", "key") == {
        "claude": "Claude"
    }
    assert redis.setex_calls[0][0] == "model_registry:anthropic"


@pytest.mark.asyncio
async def test_invalidate_model_registry_deletes_one_or_all_providers() -> None:
    redis = FakeRedis()

    await model_registry.invalidate_model_registry(redis, "openai")
    await model_registry.invalidate_model_registry(redis)

    assert redis.deleted == [
        "model_registry:openai",
        "model_registry:openai",
        "model_registry:anthropic",
        "model_registry:google",
    ]


@pytest.mark.asyncio
async def test_fetch_model_mapping_routes_by_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def anthropic(api_key):
        return {"claude": "Claude"}

    async def openai(api_key):
        return {"gpt": "gpt"}

    monkeypatch.setattr(model_registry, "_fetch_anthropic_models", anthropic)
    monkeypatch.setattr(model_registry, "_fetch_openai_models", openai)

    assert await model_registry._fetch_model_mapping("anthropic", "key") == {
        "claude": "Claude"
    }
    assert await model_registry._fetch_model_mapping("openai", "key") == {"gpt": "gpt"}
    assert await model_registry._fetch_model_mapping("unknown", "key") == {}


@pytest.mark.asyncio
async def test_fetch_anthropic_models_parses_display_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": [
                    {"id": "claude-opus-4-5-20251101", "display_name": "Claude Opus 4.5"},
                    {"id": "missing-name"},
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, *, headers, timeout):
            assert url == "https://api.anthropic.com/v1/models"
            assert headers["x-api-key"] == "key"
            assert timeout == 30.0
            return FakeResponse()

    monkeypatch.setattr(model_registry.httpx, "AsyncClient", FakeClient)

    assert await model_registry._fetch_anthropic_models("key") == {
        "claude-opus-4-5-20251101": "Claude Opus 4.5"
    }


@pytest.mark.asyncio
async def test_fetch_openai_models_parses_and_normalizes_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": [
                    {"id": "gpt-4o-2024-11-20"},
                    {"id": "gpt-4.1"},
                    {"object": "model"},
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, *, headers, timeout):
            assert url == "https://api.openai.com/v1/models"
            assert headers["Authorization"] == "Bearer key"
            assert timeout == 30.0
            return FakeResponse()

    monkeypatch.setattr(model_registry.httpx, "AsyncClient", FakeClient)

    assert await model_registry._fetch_openai_models("key") == {
        "gpt-4o-2024-11-20": "gpt-4o",
        "gpt-4.1": "gpt-4.1",
    }


@pytest.mark.asyncio
async def test_fetch_models_reraises_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("GET", "https://api.openai.com/v1/models")
    response = httpx.Response(401, request=request, text="no")

    class FakeResponse:
        text = "no"
        status_code = 401

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError("bad", request=request, response=response)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(model_registry.httpx, "AsyncClient", FakeClient)

    with pytest.raises(httpx.HTTPStatusError):
        await model_registry._fetch_openai_models("key")
