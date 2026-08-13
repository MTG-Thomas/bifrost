"""
Tool Registry Service

Provides AI agent tools from workflows with type='tool'.
Converts workflow metadata to LLM-friendly tool definitions.
"""

import logging
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import Workflow

logger = logging.getLogger(__name__)


def _normalize_tool_name(name: str, category: str | None = None) -> str:
    """
    Convert workflow name to valid API tool name with category prefix.

    Anthropic API requires tool names to match ^[a-zA-Z0-9_-]{1,128}$
    This converts names like "Add Comment (Demo)" to "halopsa_add_comment_demo"
    when category="HaloPSA", or "wf_add_comment_demo" when category is None/General.

    The prefix prevents collisions between workflow tools and system tools
    (e.g., a workflow named "Execute Workflow" won't shadow the system tool).
    """
    # Determine prefix from category
    if category and category.lower() != "general":
        prefix = category.lower().strip()
        prefix = re.sub(r"[\s\-]+", "_", prefix)
        prefix = re.sub(r"[^a-z0-9_]", "", prefix)
        prefix = re.sub(r"_+", "_", prefix)
        prefix = prefix.strip("_")
    else:
        prefix = "wf"

    name = name.lower().strip()
    # Replace spaces and hyphens with underscores
    name = re.sub(r"[\s\-]+", "_", name)
    # Remove invalid characters (keep only alphanumeric and underscore)
    name = re.sub(r"[^a-z0-9_]", "", name)
    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name)
    # Strip leading/trailing underscores
    name = name.strip("_")
    return f"{prefix}_{name}"


@dataclass
class ToolDefinition:
    """Tool definition in LLM-friendly format."""

    id: UUID
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema format
    workflow_name: str  # Original workflow name for execution
    category: str | None = None


@dataclass
class RegisteredTool:
    """Registered tool with full workflow metadata."""

    id: UUID
    name: str
    description: str
    category: str
    parameters_schema: list[dict[str, Any]] | dict[str, Any]
    file_path: str
    function_name: str


def _map_workflow_type_to_json_schema(param_type: str) -> str:
    """Map Bifrost's stored workflow parameter types to JSON Schema types."""
    type_map = {
        "string": "string",
        "str": "string",
        "int": "integer",
        "integer": "integer",
        "float": "number",
        "number": "number",
        "bool": "boolean",
        "boolean": "boolean",
        "json": "object",
        "dict": "object",
        "object": "object",
        "list": "array",
        "array": "array",
    }
    return type_map.get(param_type.lower(), "string")


def workflow_parameters_to_json_schema(
    parameters_schema: list[dict[str, Any]] | dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the complete JSON Schema exposed for a workflow tool.

    Workflow signatures are normally stored as a list of Bifrost parameter
    records. Some imported or future records may already contain a complete
    JSON Schema object; those must pass through without being flattened to
    primitive ``type`` and ``description`` fields.

    The returned object is detached from the ORM JSON value so consumers may
    safely hand it to an SDK that normalizes or mutates schemas internally.
    """
    if isinstance(parameters_schema, dict):
        return deepcopy(parameters_schema)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param in parameters_schema or []:
        param_name = param.get("name")
        if not param_name:
            continue

        json_type = _map_workflow_type_to_json_schema(
            str(param.get("type", "string"))
        )
        property_schema: dict[str, Any] = {
            "type": json_type,
            "description": (
                param.get("label")
                or param.get("description")
                or param_name
            ),
        }

        if json_type == "array":
            property_schema["items"] = {"type": "string"}
        elif json_type == "object":
            property_schema["additionalProperties"] = True

        options = param.get("options")
        if options:
            property_schema["enum"] = [
                deepcopy(option["value"])
                for option in options
                if "value" in option
            ]

        if "default_value" in param and param["default_value"] is not None:
            property_schema["default"] = deepcopy(param["default_value"])

        properties[param_name] = property_schema
        if param.get("required", False):
            required.append(param_name)

    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        result["required"] = required
    return result


def workflow_json_schema_to_parameter_records(
    parameters_schema: list[dict[str, Any]] | dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Project stored input schemas onto the legacy workflow parameter DTO.

    Existing rows already store this list representation and pass through
    unchanged. Newly indexed rows store complete JSON Schema objects; API/CLI
    consumers retain their established list-shaped contract through this
    deliberately lossy presentation adapter.
    """
    if not isinstance(parameters_schema, dict):
        return deepcopy(parameters_schema or [])

    properties = parameters_schema.get("properties")
    if not isinstance(properties, dict):
        return []
    required = set(parameters_schema.get("required") or [])
    records: list[dict[str, Any]] = []

    def primary_schema(schema: dict[str, Any]) -> dict[str, Any]:
        variants = schema.get("anyOf")
        if isinstance(variants, list):
            for variant in variants:
                if isinstance(variant, dict) and variant.get("type") != "null":
                    return variant
        return schema

    type_map = {
        "string": "string",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "array": "list",
        "object": "json",
    }
    for name, raw_schema in properties.items():
        if not isinstance(name, str) or not isinstance(raw_schema, dict):
            continue
        resolved = primary_schema(raw_schema)
        json_type = resolved.get("type")
        if isinstance(json_type, list):
            json_type = next(
                (value for value in json_type if value != "null"),
                None,
            )
        record: dict[str, Any] = {
            "name": name,
            "type": type_map.get(str(json_type), "json"),
            "required": name in required,
        }
        title = raw_schema.get("title")
        if isinstance(title, str):
            record["label"] = title
        description = raw_schema.get("description")
        if isinstance(description, str):
            record["description"] = description
        if "default" in raw_schema:
            record["default_value"] = deepcopy(raw_schema["default"])
        enum = resolved.get("enum")
        if isinstance(enum, list):
            record["options"] = [
                {"label": str(value), "value": str(value)}
                for value in enum
            ]
        records.append(record)
    return records


class ToolRegistry:
    """
    Registry for AI agent tools.

    Provides tools from workflows with type='tool'.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all_tools(self) -> Sequence[RegisteredTool]:
        """
        Get all registered tools.

        Returns:
            List of RegisteredTool objects
        """
        result = await self.session.execute(
            select(Workflow)
            .where(Workflow.is_active.is_(True))
            .where(Workflow.type == "tool")
            .order_by(Workflow.name, Workflow.id)
        )
        workflows = result.scalars().all()

        return [
            RegisteredTool(
                id=w.id,
                name=w.name,
                description=w.tool_description or w.description or "",
                category=w.category,
                parameters_schema=w.parameters_schema,
                file_path=w.path,
                function_name=w.function_name,
            )
            for w in workflows
        ]

    async def get_tools_by_ids(self, tool_ids: list[UUID]) -> Sequence[RegisteredTool]:
        """
        Get specific tools by their IDs.

        Args:
            tool_ids: List of workflow UUIDs to retrieve

        Returns:
            List of RegisteredTool objects
        """
        if not tool_ids:
            return []

        # First, check what workflows exist with these IDs (for debugging)
        all_workflows_result = await self.session.execute(
            select(Workflow.id, Workflow.name, Workflow.is_active, Workflow.type)
            .where(Workflow.id.in_(tool_ids))
        )
        all_workflows = all_workflows_result.fetchall()
        for w in all_workflows:
            logger.debug(
                f"Workflow '{w.name}' (id={w.id}): is_active={w.is_active}, type={w.type}"
            )
            if w.type != "tool":
                logger.warning(
                    f"Workflow '{w.name}' is assigned to agent but type='{w.type}' - "
                    "it won't be available as a tool!"
                )
            if not w.is_active:
                logger.warning(
                    f"Workflow '{w.name}' is assigned to agent but is_active=False - "
                    "it won't be available as a tool!"
                )

        result = await self.session.execute(
            select(Workflow)
            .where(Workflow.id.in_(tool_ids))
            .where(Workflow.is_active.is_(True))
            .where(Workflow.type == "tool")
            .order_by(Workflow.name, Workflow.id)
        )
        workflows = result.scalars().all()
        logger.info(f"Filtered to {len(workflows)} active tools from {len(tool_ids)} requested IDs")

        return [
            RegisteredTool(
                id=w.id,
                name=w.name,
                description=w.tool_description or w.description or "",
                category=w.category,
                parameters_schema=w.parameters_schema,
                file_path=w.path,
                function_name=w.function_name,
            )
            for w in workflows
        ]

    async def get_tool_definitions(
        self, tool_ids: list[UUID] | None = None
    ) -> list[ToolDefinition]:
        """
        Get tool definitions in LLM-friendly format.

        Args:
            tool_ids: Optional list of tool IDs to filter by.
                     If None, returns all tools.

        Returns:
            List of ToolDefinition objects ready for LLM function calling
        """
        if tool_ids is not None:
            tools = await self.get_tools_by_ids(tool_ids)
        else:
            tools = await self.get_all_tools()

        return [self._to_tool_definition(t) for t in tools]

    def _to_tool_definition(self, tool: RegisteredTool) -> ToolDefinition:
        """
        Convert a RegisteredTool to LLM-friendly ToolDefinition.

        Converts workflow parameter schema to JSON Schema format
        compatible with OpenAI/Anthropic function calling.
        """
        return ToolDefinition(
            id=tool.id,
            name=_normalize_tool_name(tool.name, category=tool.category),
            description=tool.description,
            parameters=workflow_parameters_to_json_schema(tool.parameters_schema),
            workflow_name=tool.name,  # Keep original for execution lookup
            category=tool.category,
        )

    def _map_type_to_json_schema(self, param_type: str) -> str:
        """Map workflow parameter type to JSON Schema type."""
        return _map_workflow_type_to_json_schema(param_type)

    async def get_tool_by_name(self, name: str) -> RegisteredTool | None:
        """
        Get a specific tool by name.

        Args:
            name: Tool (workflow) name

        Returns:
            RegisteredTool or None if not found
        """
        result = await self.session.execute(
            select(Workflow)
            .where(Workflow.name == name)
            .where(Workflow.is_active.is_(True))
            .where(Workflow.type == "tool")
        )
        workflow = result.scalar_one_or_none()

        if not workflow:
            return None

        return RegisteredTool(
            id=workflow.id,
            name=workflow.name,
            description=workflow.tool_description or workflow.description or "",
            category=workflow.category,
            parameters_schema=workflow.parameters_schema,
            file_path=workflow.path,
            function_name=workflow.function_name,
        )

    async def get_tool_by_id(self, tool_id: UUID) -> RegisteredTool | None:
        """
        Get a specific tool by ID.

        Args:
            tool_id: Tool (workflow) UUID

        Returns:
            RegisteredTool or None if not found
        """
        result = await self.session.execute(
            select(Workflow)
            .where(Workflow.id == tool_id)
            .where(Workflow.is_active.is_(True))
            .where(Workflow.type == "tool")
        )
        workflow = result.scalar_one_or_none()

        if not workflow:
            return None

        return RegisteredTool(
            id=workflow.id,
            name=workflow.name,
            description=workflow.tool_description or workflow.description or "",
            category=workflow.category,
            parameters_schema=workflow.parameters_schema,
            file_path=workflow.path,
            function_name=workflow.function_name,
        )


def format_tools_for_openai(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """
    Format tools for OpenAI function calling API.

    Args:
        tools: List of ToolDefinition objects

    Returns:
        List of OpenAI tool definitions
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


def format_tools_for_anthropic(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """
    Format tools for Anthropic Claude tool use API.

    Args:
        tools: List of ToolDefinition objects

    Returns:
        List of Anthropic tool definitions
    """
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
        }
        for tool in tools
    ]
