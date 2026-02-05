"""
bifrost/workflows.py - Workflows SDK (API-only)

Provides Python API for workflow operations (list, get status).
All operations go through HTTP API endpoints.
"""

from .client import get_client
from .executions import WorkflowExecution, executions
from .models import WorkflowMetadata


class workflows:
    """
    Workflow operations.

    Allows workflows to query available workflows and execution status.

    All methods are async - await is required.
    """

    @staticmethod
    async def list() -> list[WorkflowMetadata]:
        """
        List all available workflows.

        Returns:
            list[WorkflowMetadata]: List of workflow metadata with attributes:
                - id: str - Workflow UUID
                - name: str - Workflow name (snake_case)
                - description: str | None - Human-readable description
                - category: str - Category for organization
                - tags: list[str] - Tags for categorization
                - parameters: dict - Workflow parameters
                - execution_mode: str - Execution mode
                - timeout_seconds: int - Max execution time
                - retry_policy: dict | None - Retry configuration
                - endpoint_enabled: bool - Whether exposed as HTTP endpoint
                - allowed_methods: list[str] | None - Allowed HTTP methods
                - disable_global_key: bool - Whether global API key is disabled
                - public_endpoint: bool - Whether endpoint is public
                - is_tool: bool - Whether available as AI tool
                - tool_description: str | None - AI tool description
                - time_saved: int - Minutes saved per execution
                - source_file_path: str | None - Full file path
                - relative_file_path: str | None - Workspace-relative path

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import workflows
            >>> wf_list = await workflows.list()
            >>> for wf in wf_list:
            ...     print(f"{wf.name}: {wf.description}")
        """
        client = get_client()
        response = await client.get("/api/workflows")
        response.raise_for_status()
        data = response.json()
        return [WorkflowMetadata.model_validate(wf) for wf in data]

    @staticmethod
    async def get(execution_id: str) -> WorkflowExecution:
        """
        Get execution details for a workflow.

        Args:
            execution_id: Execution ID

        Returns:
            WorkflowExecution: Execution details including status, result, logs

        Raises:
            ValueError: If execution not found
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import workflows
            >>> execution = await workflows.get("exec-123")
            >>> print(execution.status)
        """
        # Delegate to executions SDK
        return await executions.get(execution_id)
