"""Bifrost SDK — Agent invocation from workflows."""
import json
import logging
from typing import Any

from .client import get_client, raise_for_status_with_detail

logger = logging.getLogger(__name__)


class agents:
    """Agent execution operations."""

    @staticmethod
    async def run(
        agent_name: str,
        input: dict[str, Any] | None = None,
        *,
        output_schema: dict[str, Any] | None = None,
        timeout: int = 1800,
    ) -> dict[str, Any] | str:
        """Run an agent and wait for the result.

        Args:
            agent_name: Name of the agent to run.
            input: Structured input data for the agent.
            output_schema: JSON Schema for the expected output.
            timeout: Maximum seconds to wait (default 30 min).

        Returns:
            Structured dict if output_schema was provided, otherwise string.

        Raises:
            RuntimeError: If the agent run fails.
            ValueError: If the agent is not found.
        """
        client = get_client()
        response = await client.post(
            "/api/agent-runs/execute",
            json={
                "agent_name": agent_name,
                "input": input or {},
                "output_schema": output_schema,
                "timeout": timeout,
            },
        )
        raise_for_status_with_detail(response)
        data = response.json()

        if data.get("error"):
            raise RuntimeError(f"Agent run failed: {data['error']}")

        output = data.get("output")
        if output_schema and isinstance(output, str):
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                return output
        return output
