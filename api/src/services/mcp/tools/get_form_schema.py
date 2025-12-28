"""
Get Form Schema MCP Tool

Returns documentation about form structure including field types,
properties, and example JSON for creating forms.
"""

import logging
from typing import Any

from src.services.mcp.server import MCPContext

logger = logging.getLogger(__name__)

# Claude Agent SDK is optional - will be installed when using coding mode
try:
    from claude_agent_sdk import tool  # type: ignore

    HAS_CLAUDE_SDK = True
except ImportError:
    HAS_CLAUDE_SDK = False

    def tool(**kwargs: Any) -> Any:
        """Stub decorator when SDK not installed."""

        def decorator(func: Any) -> Any:
            return func

        return decorator


FORM_SCHEMA_DOCUMENTATION = """
# Bifrost Form Schema Documentation

Forms in Bifrost allow you to create user interfaces for collecting input to workflows.

## FormFieldType Values

| Type | Description |
|------|-------------|
| `text` | Single-line text input |
| `email` | Email input with validation |
| `number` | Numeric input |
| `select` | Dropdown selection (requires `options` or `data_provider_id`) |
| `checkbox` | Boolean checkbox |
| `textarea` | Multi-line text input |
| `radio` | Radio button group (requires `options`) |
| `datetime` | Date and time picker |
| `markdown` | Static markdown content display (requires `content`, no `label`) |
| `html` | Static HTML content display (requires `content`, no `label`) |
| `file` | File upload (supports `allowed_types`, `multiple`, `max_size_mb`) |

## FormField Properties

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | string | Yes | Parameter name passed to workflow |
| `label` | string | Yes* | Display label (*not required for markdown/html types) |
| `type` | FormFieldType | Yes | Field type from the list above |
| `required` | boolean | No | Whether the field must be filled (default: false) |
| `validation` | object | No | Validation rules: `pattern`, `min`, `max`, `message` |
| `data_provider_id` | UUID | No | Data provider ID for dynamic options |
| `data_provider_inputs` | object | No | Input configurations for data provider (requires data_provider_id) |
| `visibility_expression` | string | No | JavaScript expression for conditional visibility (e.g., `context.field.show === true`) |
| `options` | array | No | Static options for radio/select: `[{"value": "x", "label": "X"}]` |
| `allowed_types` | array | No | Allowed MIME types for file uploads |
| `multiple` | boolean | No | Allow multiple file uploads |
| `max_size_mb` | integer | No | Maximum file size in MB |
| `content` | string | No | Static content for markdown/HTML fields (required for these types) |
| `allow_as_query_param` | boolean | No | Allow field value to be populated from URL query parameters |
| `help_text` | string | No | Help text displayed below the field |
| `placeholder` | string | No | Placeholder text for input fields |
| `default_value` | any | No | Default value for the field |

## FormSchema Structure

```json
{
  "fields": [
    { "name": "...", "label": "...", "type": "...", ... },
    ...
  ]
}
```

Maximum 50 fields per form. Field names must be unique.

## FormCreate Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Form name (1-200 characters) |
| `description` | string | No | Form description |
| `workflow_id` | UUID string | Yes | Workflow to execute on submission |
| `launch_workflow_id` | UUID string | No | Workflow to run when form loads (for dynamic data) |
| `form_schema` | FormSchema | Yes | The form field definitions |
| `access_level` | string | No | "authenticated" or "role_based" (default: role_based) |
| `allowed_query_params` | array | No | Query parameter names allowed to inject into form context |
| `default_launch_params` | object | No | Default parameters for workflow execution |

## Important Notes

1. **Create workflow first**: The workflow must exist before creating the form with its ID
2. **Field name uniqueness**: All field names within a form must be unique
3. **Content fields**: `markdown` and `html` types require `content` property, not `label`
4. **Data providers**: If using `data_provider_inputs`, you must also set `data_provider_id`

## Example Form JSON

```json
{
  "name": "User Onboarding Form",
  "description": "Collect user information for onboarding",
  "workflow_id": "12345678-1234-1234-1234-123456789abc",
  "form_schema": {
    "fields": [
      {
        "name": "welcome_message",
        "type": "markdown",
        "content": "## Welcome!\\nPlease fill out the form below."
      },
      {
        "name": "full_name",
        "label": "Full Name",
        "type": "text",
        "required": true,
        "placeholder": "Enter your full name"
      },
      {
        "name": "email",
        "label": "Email Address",
        "type": "email",
        "required": true,
        "validation": {
          "pattern": "^[^@]+@[^@]+\\\\.[^@]+$",
          "message": "Please enter a valid email address"
        }
      },
      {
        "name": "department",
        "label": "Department",
        "type": "select",
        "required": true,
        "options": [
          {"value": "engineering", "label": "Engineering"},
          {"value": "sales", "label": "Sales"},
          {"value": "marketing", "label": "Marketing"}
        ]
      },
      {
        "name": "start_date",
        "label": "Start Date",
        "type": "datetime",
        "required": true
      },
      {
        "name": "needs_equipment",
        "label": "Request Equipment",
        "type": "checkbox",
        "help_text": "Check if you need a laptop and other equipment"
      },
      {
        "name": "resume",
        "label": "Upload Resume",
        "type": "file",
        "allowed_types": ["application/pdf", "application/msword"],
        "max_size_mb": 10
      }
    ]
  },
  "access_level": "authenticated"
}
```
"""


def get_form_schema_tool(context: MCPContext) -> Any:
    """
    Create a get_form_schema tool bound to the given context.

    Args:
        context: MCP context with user/org information

    Returns:
        Tool function for Claude Agent SDK
    """

    @tool(
        name="get_form_schema",
        description="Get documentation about Bifrost form schema structure, field types, and properties. Use this to understand how to create forms.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    async def _get_form_schema(args: dict[str, Any]) -> dict[str, Any]:
        """
        Return form schema documentation.

        Returns:
            Dict with form schema documentation
        """
        logger.info("MCP get_form_schema called")

        return {
            "content": [
                {
                    "type": "text",
                    "text": FORM_SCHEMA_DOCUMENTATION,
                }
            ]
        }

    return _get_form_schema
