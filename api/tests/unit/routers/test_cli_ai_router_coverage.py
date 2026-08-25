from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from src.core.principal import UserPrincipal
from src.models.contracts.cli import CLIAICompleteRequest
from src.routers import cli


def _user() -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="operator@example.com",
        organization_id=uuid4(),
        is_superuser=False,
    )


def _request(**kwargs) -> CLIAICompleteRequest:
    data = {
        "messages": [{"role": "user", "content": "Summarize this"}],
        "max_tokens": 50,
        "model": "test-model",
    }
    data.update(kwargs)
    return CLIAICompleteRequest(**data)


class TestCLIAIComplete:
    @pytest.mark.asyncio
    async def test_complete_returns_model_response_and_ignores_usage_failure(self) -> None:
        client = SimpleNamespace(
            provider_name="test-provider",
            model_name="fallback-model",
            complete=AsyncMock(
                return_value=SimpleNamespace(
                    content="answer",
                    input_tokens=3,
                    output_tokens=5,
                    model="chosen-model",
                )
            ),
        )

        with (
            patch("src.services.llm.get_llm_client", AsyncMock(return_value=client)),
            patch("src.routers.cli._resolve_sdk_org_id", AsyncMock(return_value=None)),
            patch("src.core.cache.get_shared_redis", AsyncMock(side_effect=RuntimeError("redis down"))),
        ):
            response = await cli.cli_ai_complete(_request(), _user(), AsyncMock())

        assert response.content == "answer"
        assert response.input_tokens == 3
        assert response.output_tokens == 5
        assert response.model == "chosen-model"
        client.complete.assert_awaited_once()
        assert client.complete.await_args.kwargs["max_tokens"] == 50
        assert client.complete.await_args.kwargs["model"] == "test-model"

    @pytest.mark.asyncio
    async def test_complete_maps_missing_llm_config_to_503(self) -> None:
        with patch("src.services.llm.get_llm_client", AsyncMock(side_effect=ValueError("no llm"))):
            with pytest.raises(HTTPException) as exc:
                await cli.cli_ai_complete(_request(), _user(), AsyncMock())

        assert exc.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert exc.value.detail == "no llm"

    @pytest.mark.asyncio
    async def test_complete_maps_provider_auth_error_to_401(self) -> None:
        auth_error = type("AuthenticationError", (Exception,), {"__module__": "openai"})
        client = SimpleNamespace(complete=AsyncMock(side_effect=auth_error("bad key")))

        with patch("src.services.llm.get_llm_client", AsyncMock(return_value=client)):
            with pytest.raises(HTTPException) as exc:
                await cli.cli_ai_complete(_request(), _user(), AsyncMock())

        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "OpenAI API key is invalid" in exc.value.detail

    @pytest.mark.asyncio
    async def test_complete_maps_unexpected_error_to_500_without_detail_leak(self) -> None:
        client = SimpleNamespace(complete=AsyncMock(side_effect=RuntimeError("secret details")))

        with patch("src.services.llm.get_llm_client", AsyncMock(return_value=client)):
            with pytest.raises(HTTPException) as exc:
                await cli.cli_ai_complete(_request(), _user(), AsyncMock())

        assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert exc.value.detail == "AI completion failed. See server logs for details."


class TestCLIAIStreamAndInfo:
    @pytest.mark.asyncio
    async def test_stream_yields_delta_done_and_done_sentinel(self) -> None:
        async def stream(**kwargs):
            yield SimpleNamespace(type="delta", content="hel")
            yield SimpleNamespace(type="done", input_tokens=2, output_tokens=4)

        client = SimpleNamespace(
            provider_name="test-provider",
            model_name="stream-model",
            stream=stream,
        )

        with (
            patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=None)),
            patch("src.services.llm.get_llm_client", AsyncMock(return_value=client)),
            patch("src.core.cache.get_shared_redis", AsyncMock(side_effect=RuntimeError("redis down"))),
        ):
            response = await cli.cli_ai_stream(_request(), _user(), AsyncMock())
            chunks = [chunk async for chunk in response.body_iterator]

        assert response.media_type == "text/event-stream"
        assert chunks == [
            'data: {"content": "hel"}\n\n',
            'data: {"done": true, "input_tokens": 2, "output_tokens": 4}\n\n',
            "data: [DONE]\n\n",
        ]

    @pytest.mark.asyncio
    async def test_stream_reports_configuration_error_as_sse_error(self) -> None:
        with (
            patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=None)),
            patch("src.services.llm.get_llm_client", AsyncMock(side_effect=ValueError("no llm"))),
        ):
            response = await cli.cli_ai_stream(_request(), _user(), AsyncMock())
            chunks = [chunk async for chunk in response.body_iterator]

        assert chunks == [
            'data: {"error": "AI stream is unavailable. See server logs for details."}\n\n'
        ]

    @pytest.mark.asyncio
    async def test_info_returns_config_and_maps_missing_config_to_404(self) -> None:
        config = SimpleNamespace(provider="openai", model="gpt-test", max_tokens=123)

        with patch("src.services.llm.factory.get_llm_config", AsyncMock(return_value=config)):
            response = await cli.cli_ai_info(_user(), AsyncMock())

        assert response.provider == "openai"
        assert response.model == "gpt-test"

        with patch(
            "src.services.llm.factory.get_llm_config",
            AsyncMock(side_effect=ValueError("missing config")),
        ):
            with pytest.raises(HTTPException) as exc:
                await cli.cli_ai_info(_user(), AsyncMock())

        assert exc.value.status_code == status.HTTP_404_NOT_FOUND
        assert exc.value.detail == "missing config"
