"""
Data Provider MCP Tools

Tools for getting schema documentation for data providers.
Note: Data providers are now stored in the workflows table with type='data_provider'.
Use list_workflows to see data providers.
"""

import logging
from typing import Any

from mcp.types import CallToolResult

from src.services.mcp_server.tool_decorator import system_tool
from src.services.mcp_server.tool_registry import ToolCategory
from src.services.mcp_server.tool_result import success_result

logger = logging.getLogger(__name__)


@system_tool(
    id="get_data_provider_schema",
    name="Get Data Provider Schema",
    description="Get documentation about data provider structure, decorators, and examples.",
    category=ToolCategory.DATA_PROVIDER,
    default_enabled_for_coding_agent=True,
    is_restricted=True,
    input_schema={"type": "object", "properties": {}, "required": []},
)
async def get_data_provider_schema(context: Any) -> CallToolResult:  # noqa: ARG001
    """Get documentation about data provider structure and decorators generated from Pydantic models."""
    from src.models.contracts.workflows import DataProviderMetadata, WorkflowParameter
    from src.services.mcp_server.schema_utils import models_to_markdown

    # Generate model documentation
    model_docs = models_to_markdown([
        (DataProviderMetadata, "DataProviderMetadata"),
        (WorkflowParameter, "WorkflowParameter (input parameters)"),
    ], "Data Provider Schema Documentation")

    # Data provider-specific documentation
    usage_docs = """
## Return Format

Data providers must return a list of objects with label/value pairs:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| label | string | Yes | Text shown to user in dropdown |
| value | string | Yes | Value stored when selected |
| metadata | object | No | Optional extra data for workflows |

## Using in Forms

Reference a data provider in form field definitions:

```json
{
  "name": "customer",
  "type": "select",
  "label": "Select Customer",
  "data_provider_id": "uuid-of-provider",
  "data_provider_inputs": {
    "department_id": "{{department}}"
  }
}
```

## Viewing Data Providers

Data providers are stored as workflows with type='data_provider'.
Use `list_workflows` with type filter to see all available data providers.

## SDK Documentation

For `@data_provider` decorator documentation and examples, use `get_sdk_schema`.
"""

    schema_doc = model_docs + usage_docs
    return success_result("Data provider schema documentation", {"schema": schema_doc})
