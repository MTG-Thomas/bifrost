from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import SecretStr

from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
from src.services.execution.cloudflare_executor import (
    CloudflareExecutorConfigurationError,
    CloudflarePythonExecutor,
    CloudflareWorkflowCompatibilityError,
    validate_cloudflare_workflow_source,
)


def test_compatibility_gate_rejects_process_and_relative_imports() -> None:
    with pytest.raises(CloudflareWorkflowCompatibilityError, match="subprocess"):
        validate_cloudflare_workflow_source("import subprocess")
    with pytest.raises(CloudflareWorkflowCompatibilityError, match="relative"):
        validate_cloudflare_workflow_source("from .helpers import value")


def test_compatibility_gate_allows_pypi_imports() -> None:
    validate_cloudflare_workflow_source(
        "from pydantic import BaseModel\nimport httpx\n"
    )


@pytest.mark.asyncio
async def test_executor_posts_authenticated_protocol_and_validates_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer shared-token"
        body = __import__("json").loads(request.content)
        assert body["version"] == 1
        assert body["function_name"] == "run"
        return httpx.Response(
            200,
            json={
                "execution_id": body["execution_id"],
                "success": True,
                "result": {"answer": 42},
                "duration_ms": 3,
            },
        )

    settings = SimpleNamespace(
        cloudflare_python_executor_url="https://executor.example/run",
        cloudflare_python_executor_token=SecretStr("shared-token"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        executor = CloudflarePythonExecutor(settings, client=client)  # type: ignore[arg-type]
        result = await executor.execute(
            execution_id="execution-1",
            source="async def run(): return 42",
            function_name="run",
            parameters={},
            context={},
            timeout_seconds=30,
        )

    assert result["result"] == {"answer": 42}


@pytest.mark.asyncio
async def test_executor_requires_explicit_configuration() -> None:
    settings = SimpleNamespace(
        cloudflare_python_executor_url=None,
        cloudflare_python_executor_token=None,
    )
    with pytest.raises(CloudflareExecutorConfigurationError):
        await CloudflarePythonExecutor(settings).execute(  # type: ignore[arg-type]
            execution_id="execution-1",
            source="def run(): return 42",
            function_name="run",
            parameters={},
            context={},
            timeout_seconds=30,
        )


@pytest.mark.asyncio
async def test_consumer_routes_cloudflare_result_through_existing_handler() -> None:
    with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
        consumer = WorkflowExecutionConsumer()
    consumer._pool = SimpleNamespace(route_execution=AsyncMock())
    consumer._handle_result = AsyncMock()  # type: ignore[method-assign]

    remote_result = {
        "execution_id": "execution-1",
        "success": True,
        "result": {"answer": 42},
    }
    with (
        patch(
            "src.services.execution.cloudflare_executor.load_repo_workflow_source",
            AsyncMock(return_value="async def run(): return 42"),
        ),
        patch(
            "src.services.execution.cloudflare_executor.CloudflarePythonExecutor.execute",
            AsyncMock(return_value=remote_result),
        ) as execute,
    ):
        await consumer._route_execution_backend(
            execution_backend="cloudflare-python",
            execution_id="execution-1",
            workflow_id="workflow-1",
            workflow_name="remote workflow",
            workflow_function_name="run",
            file_path="workflows/remote.py",
            parameters={"value": 21},
            context_data={
                "caller": {"user_id": "user-1"},
                "organization": {"id": "org-1"},
                "startup": None,
                "event": None,
                "roi": {"time_saved": 2, "value": 3},
            },
            timeout_seconds=30,
            is_script=False,
            solution_id=None,
        )

    execute.assert_awaited_once()
    consumer._pool.route_execution.assert_not_awaited()
    consumer._handle_result.assert_awaited_once_with(remote_result)


@pytest.mark.asyncio
async def test_consumer_keeps_process_backend_on_existing_pool() -> None:
    with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
        consumer = WorkflowExecutionConsumer()
    consumer._pool = SimpleNamespace(route_execution=AsyncMock())
    consumer._handle_result = AsyncMock()  # type: ignore[method-assign]
    context = {"execution_id": "execution-1"}

    await consumer._route_execution_backend(
        execution_backend="process",
        execution_id="execution-1",
        workflow_id="workflow-1",
        workflow_name="local workflow",
        workflow_function_name="run",
        file_path="workflows/local.py",
        parameters={},
        context_data=context,
        timeout_seconds=30,
        is_script=False,
        solution_id=None,
    )

    consumer._pool.route_execution.assert_awaited_once_with(
        execution_id="execution-1", context=context
    )
    consumer._handle_result.assert_not_awaited()
