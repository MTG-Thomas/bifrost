"""
Validate Form Schema MCP Tool

Validates a form JSON structure against Pydantic models and
returns detailed validation errors or success confirmation.
"""

import json
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


def validate_form_schema_tool(context: MCPContext) -> Any:
    """
    Create a validate_form_schema tool bound to the given context.

    Args:
        context: MCP context with user/org information

    Returns:
        Tool function for Claude Agent SDK
    """

    @tool(
        name="validate_form_schema",
        description="Validate a form JSON structure against the Bifrost form schema. Returns detailed validation errors or success confirmation.",
        input_schema={
            "type": "object",
            "properties": {
                "form_json": {
                    "type": "string",
                    "description": "The form JSON to validate (as a string)",
                },
            },
            "required": ["form_json"],
        },
    )
    async def _validate_form_schema(args: dict[str, Any]) -> dict[str, Any]:
        """
        Validate a form JSON structure.

        Args:
            args: Tool arguments containing:
                - form_json: The JSON string to validate

        Returns:
            Dict with validation results
        """
        from pydantic import ValidationError

        from src.models.contracts.forms import FormCreate, FormSchema

        form_json_str = args.get("form_json")

        if not form_json_str:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Error: form_json is required",
                    }
                ]
            }

        logger.info("MCP validate_form_schema called")

        # Step 1: Parse JSON
        try:
            if isinstance(form_json_str, str):
                form_data = json.loads(form_json_str)
            else:
                form_data = form_json_str
        except json.JSONDecodeError as e:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"**JSON Parse Error**\n\n{str(e)}",
                    }
                ]
            }

        errors: list[str] = []

        # Step 2: Validate against Pydantic models
        # Try FormCreate first (full form definition)
        if "form_schema" in form_data:
            try:
                FormCreate.model_validate(form_data)
            except ValidationError as e:
                for error in e.errors():
                    loc = " -> ".join(str(x) for x in error["loc"])
                    msg = error["msg"]
                    errors.append(f"- `{loc}`: {msg}")
        elif "fields" in form_data:
            # Just validating FormSchema (the fields part)
            try:
                FormSchema.model_validate(form_data)
            except ValidationError as e:
                for error in e.errors():
                    loc = " -> ".join(str(x) for x in error["loc"])
                    msg = error["msg"]
                    errors.append(f"- `{loc}`: {msg}")
        else:
            errors.append(
                "- Missing required structure: needs either `form_schema` (for full form) or `fields` (for schema only)"
            )

        # Step 3: Additional semantic validations
        if not errors:
            # Get fields from the data
            if "form_schema" in form_data and "fields" in form_data.get(
                "form_schema", {}
            ):
                fields = form_data["form_schema"]["fields"]
            elif "fields" in form_data:
                fields = form_data["fields"]
            else:
                fields = []

            # Check field names are unique
            field_names = [f.get("name") for f in fields if f.get("name")]
            if len(field_names) != len(set(field_names)):
                seen = set()
                duplicates = []
                for name in field_names:
                    if name in seen:
                        duplicates.append(name)
                    seen.add(name)
                errors.append(
                    f"- Duplicate field names: {', '.join(set(duplicates))}"
                )

            # Check workflow_id is present for full form creation
            if "form_schema" in form_data and not form_data.get("workflow_id"):
                # This is a warning, not an error - workflow may be added later
                pass

        # Format response
        if errors:
            error_text = "**Form Validation Failed**\n\n"
            error_text += "The following issues were found:\n\n"
            error_text += "\n".join(errors)
            error_text += "\n\n---\n\nUse `get_form_schema` to see the expected structure and field documentation."

            return {
                "content": [
                    {
                        "type": "text",
                        "text": error_text,
                    }
                ]
            }
        else:
            # Count fields for summary
            if "form_schema" in form_data:
                fields = form_data.get("form_schema", {}).get("fields", [])
            elif "fields" in form_data:
                fields = form_data.get("fields", [])
            else:
                fields = []

            field_count = len(fields)
            required_count = sum(1 for f in fields if f.get("required"))

            success_text = "**Form Validation Successful**\n\n"
            success_text += f"- **Fields:** {field_count}\n"
            success_text += f"- **Required fields:** {required_count}\n"

            if "form_schema" in form_data:
                success_text += f"- **Form name:** {form_data.get('name', 'Not specified')}\n"
                if form_data.get("workflow_id"):
                    success_text += f"- **Workflow ID:** {form_data.get('workflow_id')}\n"
                else:
                    success_text += "\n**Note:** No `workflow_id` specified. You'll need to add the workflow ID after creating the workflow.\n"

            success_text += "\nThe form structure is valid and ready to be saved."

            return {
                "content": [
                    {
                        "type": "text",
                        "text": success_text,
                    }
                ]
            }

    return _validate_form_schema
