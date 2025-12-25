"""
Workflow and Data Provider Decorators - Standalone SDK Version

Decorators for attaching metadata to workflows and data providers.
No registration - metadata is stored on the function and discovered dynamically.

Parameter information is derived from function signatures - no @param decorator needed.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class WorkflowParameter:
    """Metadata about a workflow parameter derived from function signature."""

    name: str
    type_hint: str
    default: Any | None = None
    required: bool = True
    description: str | None = None
    ui_type: str = "text"
    options: list[str] | None = None


@dataclass
class WorkflowMetadata:
    """Metadata attached to workflow functions by the @workflow decorator."""

    # Identity
    id: str | None = None
    name: str = ""
    description: str = ""
    category: str = "General"
    tags: list[str] = field(default_factory=list)

    # Parameters (derived from function signature)
    parameters: list[WorkflowParameter] = field(default_factory=list)

    # Execution
    execution_mode: Literal["sync", "async"] | None = None
    timeout_seconds: int = 1800

    # Retry
    retry_policy: dict[str, Any] | None = None

    # Scheduling
    schedule: str | None = None

    # HTTP Endpoint
    endpoint_enabled: bool = False
    allowed_methods: list[str] = field(default_factory=lambda: ["POST"])
    disable_global_key: bool = False
    public_endpoint: bool = False

    # Tool Configuration
    is_tool: bool = False
    tool_description: str | None = None

    # Economics
    time_saved: int = 0
    value: float = 0.0

    # Source info (set by discovery)
    source_file_path: str | None = None
    relative_file_path: str | None = None


@dataclass
class DataProviderMetadata:
    """Metadata attached to data provider functions by the @data_provider decorator."""

    name: str = ""
    description: str = ""
    parameters: list[WorkflowParameter] = field(default_factory=list)
    source_file_path: str | None = None


def workflow(
    _func: Callable | None = None,
    *,
    # Identity
    id: str | None = None,
    name: str | None = None,
    description: str | None = None,
    category: str = "General",
    tags: list[str] | None = None,
    # Execution
    execution_mode: Literal["sync", "async"] | None = None,
    timeout_seconds: int = 1800,
    # Retry
    retry_policy: dict[str, Any] | None = None,
    # Scheduling
    schedule: str | None = None,
    # HTTP Endpoint Configuration
    endpoint_enabled: bool = False,
    allowed_methods: list[str] | None = None,
    disable_global_key: bool = False,
    public_endpoint: bool = False,
    # Tool Configuration
    is_tool: bool = False,
    tool_description: str | None = None,
    # Economics
    time_saved: int = 0,
    value: float = 0.0,
) -> Callable[[F], F] | F:
    """
    Decorator for registering workflow functions.

    Parameters are automatically derived from function signatures.

    Usage:
        @workflow
        async def greet_user(name: str, count: int = 1) -> dict:
            '''Greet a user multiple times.'''
            return {"greetings": [f"Hello {name}!" for _ in range(count)]}

        @workflow(category="Admin", tags=["user", "m365"])
        async def onboard_user(email: str, license_type: str = "E3") -> dict:
            '''Onboard a new M365 user.'''
            ...

    Args:
        id: Persistent UUID (written by discovery watcher)
        name: Workflow name (defaults to function name)
        description: Description (defaults to first line of docstring)
        category: Category for organization (default: "General")
        tags: Optional list of tags for filtering
        execution_mode: "sync" | "async" | None (auto-select)
        timeout_seconds: Max execution time (default: 1800)
        retry_policy: Dict with retry config
        schedule: Cron expression for scheduled workflows
        endpoint_enabled: Whether to expose as HTTP endpoint
        allowed_methods: HTTP methods allowed (default: ["POST"])
        disable_global_key: If True, only workflow-specific API keys work
        public_endpoint: If True, skip authentication
        is_tool: If True, available as AI agent tool
        tool_description: LLM-friendly description for tools
        time_saved: Minutes saved per execution
        value: Flexible value unit

    Returns:
        Decorated function with _workflow_metadata attribute
    """

    def decorator(func: F) -> F:
        # Extract description from docstring
        func_description = description
        if func_description is None and func.__doc__:
            func_description = func.__doc__.split("\n")[0].strip()

        # Create metadata
        metadata = WorkflowMetadata(
            id=id,
            name=name or func.__name__,
            description=func_description or "",
            category=category,
            tags=tags or [],
            execution_mode=execution_mode,
            timeout_seconds=timeout_seconds,
            retry_policy=retry_policy,
            schedule=schedule,
            endpoint_enabled=endpoint_enabled,
            allowed_methods=allowed_methods or ["POST"],
            disable_global_key=disable_global_key,
            public_endpoint=public_endpoint,
            is_tool=is_tool,
            tool_description=tool_description,
            time_saved=time_saved,
            value=value,
        )

        # Attach metadata to function
        func._workflow_metadata = metadata  # type: ignore
        return func

    if _func is not None:
        return decorator(_func)
    return decorator


def data_provider(
    _func: Callable | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Callable[[F], F] | F:
    """
    Decorator for data provider functions.

    Data providers fetch reference data that can be used in forms.

    Usage:
        @data_provider
        async def get_departments() -> list[str]:
            '''Get list of departments.'''
            return ["Engineering", "Sales", "Marketing"]

    Args:
        name: Provider name (defaults to function name)
        description: Description (defaults to first line of docstring)

    Returns:
        Decorated function with _data_provider_metadata attribute
    """

    def decorator(func: F) -> F:
        func_description = description
        if func_description is None and func.__doc__:
            func_description = func.__doc__.split("\n")[0].strip()

        metadata = DataProviderMetadata(
            name=name or func.__name__,
            description=func_description or "",
        )

        func._data_provider_metadata = metadata  # type: ignore
        return func

    if _func is not None:
        return decorator(_func)
    return decorator
