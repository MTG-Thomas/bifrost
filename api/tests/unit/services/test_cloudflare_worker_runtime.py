from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest
import httpx
from pydantic import SecretStr

from src.services.execution.cloudflare_executor import CloudflarePythonExecutor


RUNTIME_PATH = (
    Path("/workspace/cloudflare")
    / "python-executor"
    / "src"
    / "runtime.py"
)
SPEC = importlib.util.spec_from_file_location("bifrost_cloudflare_runtime", RUNTIME_PATH)
assert SPEC and SPEC.loader
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


@pytest.mark.asyncio
async def test_runtime_executes_async_decorated_workflow_with_pypi_package() -> None:
    source = """
from bifrost import context, workflow
from pydantic import BaseModel

class Output(BaseModel):
    greeting: str
    org: str

@workflow(name="remote_greeting")
async def greet(name: str) -> dict:
    print(f"greeting {name}")
    return Output(greeting=f"Hello {name}", org=context.organization.name).model_dump()
"""
    result = await runtime.execute_payload(
        {
            "version": 1,
            "execution_id": "execution-1",
            "source": source,
            "function_name": "greet",
            "parameters": {"name": "Cloudflare"},
            "context": {
                "organization": {"name": "Midtown"},
                "roi": {"time_saved": 4, "value": 5},
            },
        }
    )

    assert result["success"] is True
    assert result["result"] == {
        "greeting": "Hello Cloudflare",
        "org": "Midtown",
    }
    assert result["logs"] == ["greeting Cloudflare"]
    assert result["roi"] == {"time_saved": 4, "value": 5}


@pytest.mark.asyncio
async def test_runtime_context_is_isolated_between_concurrent_requests() -> None:
    source = """
import asyncio
from bifrost import context

async def identify(delay: float) -> str:
    await asyncio.sleep(delay)
    return context.caller.name
"""

    async def invoke(execution_id: str, name: str, delay: float):
        return await runtime.execute_payload(
            {
                "version": 1,
                "execution_id": execution_id,
                "source": source,
                "function_name": "identify",
                "parameters": {"delay": delay},
                "context": {"caller": {"name": name}},
            }
        )

    first, second = await asyncio.gather(
        invoke("execution-1", "Ada", 0.02),
        invoke("execution-2", "Grace", 0.0),
    )
    assert first["result"] == "Ada"
    assert second["result"] == "Grace"


@pytest.mark.asyncio
async def test_bifrost_dispatcher_and_worker_share_end_to_end_protocol() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer protocol-secret"
        result = await runtime.execute_payload(__import__("json").loads(request.content))
        return httpx.Response(200, json=result)

    settings = type(
        "ExecutorSettings",
        (),
        {
            "cloudflare_python_executor_url": "https://executor.example/",
            "cloudflare_python_executor_token": SecretStr("protocol-secret"),
        },
    )()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await CloudflarePythonExecutor(  # type: ignore[arg-type]
            settings, client=client
        ).execute(
            execution_id="execution-e2e",
            source="async def multiply(value: int) -> int: return value * 2",
            function_name="multiply",
            parameters={"value": 21},
            context={"caller": {"name": "Ada"}},
            timeout_seconds=30,
        )

    assert result["success"] is True
    assert result["result"] == 42
