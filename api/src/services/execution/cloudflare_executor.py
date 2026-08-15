"""Optional HTTP bridge to the Cloudflare Python workflow executor.

The bridge deliberately accepts only one self-contained workspace module. It
does not attempt to emulate the container worker's process isolation, virtual
imports, dynamic package installation, or filesystem. Bifrost still owns queue
delivery and all durable execution state; Cloudflare only evaluates the module
and returns the existing worker result envelope.
"""

from __future__ import annotations

import ast
from typing import Any

import httpx

from src.config import Settings, get_settings


class CloudflareExecutorConfigurationError(RuntimeError):
    """Raised when a workflow opts in but the remote executor is not configured."""


class CloudflareWorkflowCompatibilityError(RuntimeError):
    """Raised before dispatch when source requires unsupported OS capabilities."""


_UNSUPPORTED_IMPORTS = {
    "ctypes",
    "fcntl",
    "multiprocessing",
    "pty",
    "resource",
    "signal",
    "socketserver",
    "subprocess",
    "threading",
}


def validate_cloudflare_workflow_source(source: str) -> None:
    """Reject capabilities known to be unavailable in a Workers isolate.

    This is intentionally a compatibility check, not a security sandbox. The
    Cloudflare isolate is the security boundary. Imports of ordinary PyPI
    packages are allowed and are resolved from the executor's deployment
    bundle; relative imports are rejected because only one module is sent.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise CloudflareWorkflowCompatibilityError(
            f"workflow source is not valid Python: {exc.msg} (line {exc.lineno})"
        ) from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                raise CloudflareWorkflowCompatibilityError(
                    "relative workspace imports are not supported by the Cloudflare MVP"
                )
            names = [node.module] if node.module else []
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        for name in names:
            root = name.split(".", 1)[0]
            if root in _UNSUPPORTED_IMPORTS:
                raise CloudflareWorkflowCompatibilityError(
                    f"module '{root}' is unavailable in Cloudflare Python Workers"
                )


async def load_repo_workflow_source(path: str) -> str:
    """Load source through the worker's canonical Redis-to-object-store path."""
    from src.core.module_cache import get_module

    cached = await get_module(path)
    if cached:
        return cached["content"]

    from src.services.repo_storage import RepoStorage

    try:
        return (await RepoStorage().read(path)).decode("utf-8")
    except Exception as exc:
        raise CloudflareWorkflowCompatibilityError(
            f"unable to load workflow source for Cloudflare dispatch: {path}"
        ) from exc


class CloudflarePythonExecutor:
    """Dispatch a workflow module to a configured Cloudflare Python Worker."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client

    async def execute(
        self,
        *,
        execution_id: str,
        source: str,
        function_name: str,
        parameters: dict[str, Any],
        context: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        validate_cloudflare_workflow_source(source)
        url = self._settings.cloudflare_python_executor_url
        token = self._settings.cloudflare_python_executor_token
        token_value = token.get_secret_value() if token is not None else ""
        if not url or not token_value:
            raise CloudflareExecutorConfigurationError(
                "cloudflare-python requires BIFROST_CLOUDFLARE_PYTHON_EXECUTOR_URL "
                "and BIFROST_CLOUDFLARE_PYTHON_EXECUTOR_TOKEN"
            )

        payload = {
            "version": 1,
            "execution_id": execution_id,
            "source": source,
            "function_name": function_name,
            "parameters": parameters,
            "context": context,
        }
        timeout = None if timeout_seconds == 0 else float(timeout_seconds)
        headers = {"Authorization": f"Bearer {token_value}"}

        if self._client is not None:
            response = await self._client.post(
                url, json=payload, headers=headers, timeout=timeout
            )
        else:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url, json=payload, headers=headers, timeout=timeout
                )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("Cloudflare executor returned a non-object response")
        if result.get("execution_id") != execution_id:
            raise RuntimeError("Cloudflare executor returned a mismatched execution_id")
        if not isinstance(result.get("success"), bool):
            raise RuntimeError("Cloudflare executor response is missing boolean success")
        return result
