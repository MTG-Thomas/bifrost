"""
Execution history SDK for Bifrost.

Provides Python API for execution history operations (list, get).

Works in two modes:
1. Platform context (inside workflows): Database queries via session factory
2. External context (via dev API key): API calls to /api/executions endpoints

All methods are async and must be awaited.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from ._context import _execution_context

if TYPE_CHECKING:
    from src.models import Execution as ExecutionORM

T = TypeVar("T")


def _from_api_response(cls: type[T], data: dict[str, Any]) -> T:
    """Create a dataclass instance from API response, ignoring unknown fields."""
    known_fields = {f.name for f in fields(cls)}
    filtered = {k: v for k, v in data.items() if k in known_fields}
    return cls(**filtered)


@dataclass
class WorkflowExecution:
    """Execution history record."""
    execution_id: str
    workflow_name: str
    executed_by: str
    executed_by_name: str
    status: str
    org_id: str | None = None
    form_id: str | None = None
    input_data: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    result_type: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    logs: list[dict[str, Any]] = field(default_factory=list)
    variables: dict[str, Any] | None = None
    session_id: str | None = None
    peak_memory_bytes: int | None = None
    cpu_total_seconds: float | None = None


def _is_platform_context() -> bool:
    """Check if running inside platform execution context."""
    return _execution_context.get() is not None


def _get_client():
    """Get the BifrostClient for API calls."""
    from .client import get_client
    return get_client()


def _execution_to_model(execution: "ExecutionORM", include_logs: bool = False) -> WorkflowExecution:
    """Convert ORM Execution to WorkflowExecution model."""
    # Build logs list if requested
    logs: list[dict[str, Any]] = []
    if include_logs and hasattr(execution, 'logs'):
        logs = [
            {
                "level": log.level,
                "message": log.message,
                "data": log.log_metadata,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            }
            for log in sorted(execution.logs, key=lambda x: x.timestamp or x.id)
        ]

    return WorkflowExecution(
        execution_id=str(execution.id),
        workflow_name=execution.workflow_name,
        org_id=str(execution.organization_id) if execution.organization_id else None,
        form_id=str(execution.form_id) if execution.form_id else None,
        executed_by=str(execution.executed_by),
        executed_by_name=execution.executed_by_name,
        status=execution.status,
        input_data=execution.parameters or {},
        result=execution.result,
        result_type=execution.result_type,
        error_message=execution.error_message,
        duration_ms=execution.duration_ms,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        logs=logs,
        variables=execution.variables,
        session_id=str(execution.session_id) if hasattr(execution, 'session_id') and execution.session_id else None,
        peak_memory_bytes=execution.peak_memory_bytes if hasattr(execution, 'peak_memory_bytes') else None,
        cpu_total_seconds=execution.cpu_total_seconds if hasattr(execution, 'cpu_total_seconds') else None,
    )


class executions:
    """
    Execution history operations.

    Allows workflows to query execution history.
    Queries Postgres directly via PgBouncer (not cached).

    All methods are async - await is required.
    """

    @staticmethod
    async def list(
        workflow_name: str | None = None,
        status: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50
    ) -> list[WorkflowExecution]:
        """
        List workflow executions with filtering.

        Platform admins see all executions in their scope.
        Regular users see only their own executions.

        In platform mode: Queries database via session factory.
        In external mode: Calls /api/executions endpoint.

        Args:
            workflow_name: Filter by workflow name (optional)
            status: Filter by status (optional)
            start_date: Filter by start date in ISO format (optional)
            end_date: Filter by end date in ISO format (optional)
            limit: Maximum number of results (default: 50, max: 1000)

        Returns:
            list[WorkflowExecution]: List of execution objects with attributes:
                - execution_id: str - Unique execution ID
                - workflow_name: str - Name of the workflow
                - org_id: str | None - Organization ID
                - form_id: str | None - Form ID if triggered by form
                - executed_by: str - User ID who executed
                - executed_by_name: str - Display name of user
                - status: ExecutionStatus - Current status
                - input_data: dict - Input parameters
                - result: dict | list | str | None - Execution result
                - result_type: str | None - How to render result
                - error_message: str | None - Error if failed
                - duration_ms: int | None - Execution duration
                - started_at, completed_at: datetime | None
                - logs: list[dict] | None - Execution logs
                - variables: dict | None - Runtime variables
                - session_id: str | None - CLI session ID
                - peak_memory_bytes, cpu_total_seconds: Resource metrics

        Raises:
            RuntimeError: If no execution context (platform mode) or missing env vars (external mode)

        Example:
            >>> from bifrost import executions
            >>> recent = await executions.list(limit=10)
            >>> for execution in recent:
            ...     print(f"{execution.workflow_name}: {execution.status}")
            >>> failed = await executions.list(status="Failed")
        """
        if _is_platform_context():
            # Platform mode: query database
            from sqlalchemy import select
            from src.core.database import get_session_factory
            from src.models import Execution
            from ._internal import get_context

            context = get_context()

            org_uuid = None
            if context.org_id and context.org_id != "GLOBAL":
                try:
                    org_uuid = UUID(context.org_id)
                except ValueError:
                    pass

            # Cap limit
            limit = min(limit, 1000)

            session_factory = get_session_factory()
            async with session_factory() as db:
                query = (
                    select(Execution)
                    .order_by(Execution.created_at.desc())
                    .limit(limit)
                )

                # Organization filter
                if org_uuid:
                    query = query.where(Execution.organization_id == org_uuid)
                else:
                    query = query.where(Execution.organization_id.is_(None))

                # User filter for non-admins
                if not context.is_platform_admin:
                    user_uuid = UUID(context.user_id)
                    query = query.where(Execution.executed_by == user_uuid)

                # Optional filters
                if workflow_name:
                    query = query.where(Execution.workflow_name == workflow_name)

                if status:
                    query = query.where(Execution.status == status)

                if start_date:
                    query = query.where(Execution.created_at >= start_date)

                if end_date:
                    query = query.where(Execution.created_at <= end_date)

                result = await db.execute(query)
                return [_execution_to_model(e) for e in result.scalars().all()]
        else:
            # External mode: call API
            client = _get_client()

            # Build query parameters
            params = {}
            if workflow_name:
                params["workflowName"] = workflow_name
            if status:
                params["status"] = status
            if start_date:
                params["startDate"] = start_date
            if end_date:
                params["endDate"] = end_date
            params["limit"] = min(limit, 1000)

            response = await client.get("/api/executions", params=params)
            response.raise_for_status()
            data = response.json()
            # API returns ExecutionsListResponse with executions array
            executions_data = data.get("executions", [])
            return [_from_api_response(WorkflowExecution, exec_data) for exec_data in executions_data]

    @staticmethod
    async def get(execution_id: str) -> WorkflowExecution:
        """
        Get execution details by ID.

        Platform admins can view any execution in their scope.
        Regular users can only view their own executions.

        In platform mode: Queries database via session factory.
        In external mode: Calls /api/executions/{id} endpoint.

        Args:
            execution_id: Execution ID (UUID)

        Returns:
            WorkflowExecution: Execution details including logs

        Raises:
            ValueError: If execution not found
            PermissionError: If access denied
            RuntimeError: If no execution context (platform mode) or missing env vars (external mode)

        Example:
            >>> from bifrost import executions
            >>> exec_details = await executions.get("exec-123")
            >>> print(exec_details.status)
            >>> print(exec_details.result)
        """
        if _is_platform_context():
            # Platform mode: query database
            from sqlalchemy import select
            from sqlalchemy.orm import joinedload
            from src.core.database import get_session_factory
            from src.models import Execution
            from ._internal import get_context

            context = get_context()
            exec_uuid = UUID(execution_id)

            org_uuid = None
            if context.org_id and context.org_id != "GLOBAL":
                try:
                    org_uuid = UUID(context.org_id)
                except ValueError:
                    pass

            session_factory = get_session_factory()
            async with session_factory() as db:
                query = (
                    select(Execution)
                    .options(joinedload(Execution.logs))
                    .where(Execution.id == exec_uuid)
                )
                result = await db.execute(query)
                execution = result.scalars().unique().first()

                if not execution:
                    raise ValueError(f"Execution not found: {execution_id}")

                # Check org access
                if org_uuid and execution.organization_id != org_uuid:
                    raise PermissionError(f"Access denied to execution: {execution_id}")

                # Check user access for non-admins
                if not context.is_platform_admin:
                    user_uuid = UUID(context.user_id)
                    if execution.executed_by != user_uuid:
                        raise PermissionError(f"Access denied to execution: {execution_id}")

                return _execution_to_model(execution, include_logs=True)
        else:
            # External mode: call API
            client = _get_client()
            response = await client.get(f"/api/executions/{execution_id}")
            if response.status_code == 404:
                raise ValueError(f"Execution not found: {execution_id}")
            elif response.status_code == 403:
                raise PermissionError(f"Access denied to execution: {execution_id}")
            response.raise_for_status()
            data = response.json()
            return _from_api_response(WorkflowExecution, data)
