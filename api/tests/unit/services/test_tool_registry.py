"""
Unit tests for ToolRegistry and tool name normalization.

Tests cover:
- Tool name normalization with category prefixing
- ToolDefinition dataclass
- ToolRegistry._to_tool_definition and _map_type_to_json_schema
- format_tools_for_openai / format_tools_for_anthropic
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest


from src.services.tool_registry import (
    RegisteredTool,
    ToolDefinition,
    ToolRegistry,
    _map_workflow_type_to_json_schema,
    _normalize_tool_name,
    format_tools_for_openai,
    format_tools_for_anthropic,
    workflow_parameters_to_json_schema,
)


# ── helpers ──────────────────────────────────────────────────────────

def _make_registered_tool(**overrides) -> RegisteredTool:
    defaults = dict(
        id=uuid4(),
        name="Test Tool",
        description="A test tool",
        category="General",
        parameters_schema=[],
        file_path="workflows/test.py",
        function_name="run",
    )
    defaults.update(overrides)
    return RegisteredTool(**defaults)


def _make_tool_definition(**overrides) -> ToolDefinition:
    defaults = dict(
        id=uuid4(),
        name="wf_test",
        description="A test tool",
        parameters={"type": "object", "properties": {}},
        workflow_name="Test",
        category=None,
    )
    defaults.update(overrides)
    return ToolDefinition(**defaults)


def _make_registry() -> ToolRegistry:
    return ToolRegistry(session=MagicMock())


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _Result:
    def __init__(self, *, rows=None, scalars=None, scalar=None):
        self._rows = rows or []
        self._scalars = scalars or []
        self._scalar = scalar

    def fetchall(self):
        return self._rows

    def scalars(self):
        return _ScalarResult(self._scalars)

    def scalar_one_or_none(self):
        return self._scalar


class _SequenceSession:
    def __init__(self, *results):
        self._results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self._results.pop(0)


def _workflow(**overrides):
    defaults = dict(
        id=uuid4(),
        name="Get Ticket",
        tool_description="Fetch ticket details",
        description="Fallback description",
        category="HaloPSA",
        parameters_schema=[{"name": "ticket_id", "type": "int", "required": True}],
        path="workflows/get_ticket.py",
        function_name="run",
        is_active=True,
        type="tool",
    )
    defaults.update(overrides)
    return type("WorkflowRow", (), defaults)()


# ── _normalize_tool_name ─────────────────────────────────────────────

class TestNormalizeToolName:

    def test_basic_name_without_category_gets_wf_prefix(self):
        assert _normalize_tool_name("Add Comment") == "wf_add_comment"

    def test_basic_name_with_none_category_gets_wf_prefix(self):
        assert _normalize_tool_name("Add Comment", category=None) == "wf_add_comment"

    def test_basic_name_with_general_category_gets_wf_prefix(self):
        assert _normalize_tool_name("Add Comment", category="General") == "wf_add_comment"

    def test_general_category_case_insensitive(self):
        assert _normalize_tool_name("Test", category="general") == "wf_test"
        assert _normalize_tool_name("Test", category="GENERAL") == "wf_test"
        assert _normalize_tool_name("Test", category="GeNeRaL") == "wf_test"

    def test_explicit_category_becomes_prefix(self):
        assert _normalize_tool_name("Add Comment", category="HaloPSA") == "halopsa_add_comment"

    def test_parentheses_stripped_from_name(self):
        assert _normalize_tool_name("Add Comment (Demo)", category="HaloPSA") == "halopsa_add_comment_demo"

    def test_leading_trailing_spaces_stripped(self):
        assert _normalize_tool_name("  spaces  ", None) == "wf_spaces"

    def test_hyphens_become_underscores(self):
        assert _normalize_tool_name("my-tool", None) == "wf_my_tool"

    def test_uppercase_lowered(self):
        assert _normalize_tool_name("UPPER case", None) == "wf_upper_case"

    def test_special_bang_at_hash_chars_removed(self):
        assert _normalize_tool_name("special!@#chars", None) == "wf_specialchars"

    def test_category_none_uses_wf_prefix(self):
        result = _normalize_tool_name("Category With Spaces", None)
        assert result.startswith("wf_")

    def test_category_with_hyphen(self):
        assert _normalize_tool_name("tool", "My-Category") == "my_category_tool"

    def test_category_with_spaces(self):
        assert _normalize_tool_name("Create Asset", category="IT Glue") == "it_glue_create_asset"

    def test_special_characters_removed_from_category(self):
        assert _normalize_tool_name("Test", category="My-Category! #1") == "my_category_1_test"

    def test_multiple_spaces_collapsed(self):
        assert _normalize_tool_name("Add   Multiple   Spaces") == "wf_add_multiple_spaces"

    def test_leading_trailing_underscores_stripped(self):
        assert _normalize_tool_name("_test_name_") == "wf_test_name"

    def test_empty_string_name(self):
        assert _normalize_tool_name("") == "wf_"

    def test_empty_category_treated_as_none(self):
        assert _normalize_tool_name("Test", category="") == "wf_test"

    def test_collision_prevention_with_system_tool_names(self):
        assert _normalize_tool_name("Execute Workflow") == "wf_execute_workflow"

    def test_collision_prevention_with_search_knowledge(self):
        assert _normalize_tool_name("Search Knowledge") == "wf_search_knowledge"

    def test_realistic_halopsa_workflow_names(self):
        assert _normalize_tool_name("List Agents", category="HaloPSA") == "halopsa_list_agents"
        assert _normalize_tool_name("Get Ticket", category="HaloPSA") == "halopsa_get_ticket"
        assert _normalize_tool_name("Add Note to Ticket", category="HaloPSA") == "halopsa_add_note_to_ticket"
        assert _normalize_tool_name("Update Asset", category="HaloPSA") == "halopsa_update_asset"

    def test_unicode_characters_removed(self):
        assert _normalize_tool_name("Créer Document", category="Système") == "systme_crer_document"

    def test_numbers_preserved(self):
        assert _normalize_tool_name("API v2 Call", category="Service123") == "service123_api_v2_call"


# ── ToolDefinition dataclass ─────────────────────────────────────────

class TestToolDefinitionCategory:

    def test_tool_definition_has_category_field(self):
        td = _make_tool_definition(category="TestCategory")
        assert td.category == "TestCategory"

    def test_tool_definition_category_defaults_to_none(self):
        td = ToolDefinition(
            id=uuid4(),
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {}},
            workflow_name="Test Tool",
        )
        assert td.category is None


# ── ToolRegistry._map_type_to_json_schema ─────────────────────────────

class TestMapTypeToJsonSchema:
    def test_string(self):
        assert _map_workflow_type_to_json_schema("string") == "string"

    def test_str(self):
        assert _map_workflow_type_to_json_schema("str") == "string"

    def test_int(self):
        assert _map_workflow_type_to_json_schema("int") == "integer"

    def test_integer(self):
        assert _map_workflow_type_to_json_schema("integer") == "integer"

    def test_float(self):
        assert _map_workflow_type_to_json_schema("float") == "number"

    def test_bool(self):
        assert _map_workflow_type_to_json_schema("bool") == "boolean"

    def test_json(self):
        assert _map_workflow_type_to_json_schema("json") == "object"

    def test_dict(self):
        assert _map_workflow_type_to_json_schema("dict") == "object"

    def test_list(self):
        assert _map_workflow_type_to_json_schema("list") == "array"

    def test_unknown_falls_back_to_string(self):
        assert _map_workflow_type_to_json_schema("unknown_type") == "string"

    def test_case_insensitive(self):
        assert _map_workflow_type_to_json_schema("STRING") == "string"
        assert _map_workflow_type_to_json_schema("Int") == "integer"
        assert _map_workflow_type_to_json_schema("BOOL") == "boolean"


# ── ToolRegistry._to_tool_definition ──────────────────────────────────

class TestToToolDefinition:

    def setup_method(self):
        self.registry = _make_registry()

    def test_converts_registered_tool_to_definition(self):
        tool_id = uuid4()
        tool = _make_registered_tool(
            id=tool_id,
            name="Add Comment",
            description="Add a comment to a ticket",
            category="HaloPSA",
            parameters_schema=[
                {"name": "ticket_id", "type": "int", "label": "Ticket ID", "required": True},
                {"name": "comment", "type": "string", "label": "Comment Body", "required": True},
            ],
        )

        result = self.registry._to_tool_definition(tool)

        assert isinstance(result, ToolDefinition)
        assert result.id == tool_id
        assert result.name == "halopsa_add_comment"
        assert result.description == "Add a comment to a ticket"
        assert result.workflow_name == "Add Comment"
        assert result.category == "HaloPSA"

    def test_json_schema_structure(self):
        tool = _make_registered_tool(
            parameters_schema=[
                {"name": "ticket_id", "type": "int", "label": "Ticket ID", "required": True},
                {"name": "note", "type": "string", "label": "Note Text", "required": False},
            ],
        )

        result = self.registry._to_tool_definition(tool)

        assert result.parameters["type"] == "object"
        props = result.parameters["properties"]
        assert "ticket_id" in props
        assert props["ticket_id"]["type"] == "integer"
        assert props["ticket_id"]["description"] == "Ticket ID"
        assert "note" in props
        assert props["note"]["type"] == "string"
        assert result.parameters["required"] == ["ticket_id"]

    def test_required_list_omitted_when_empty(self):
        tool = _make_registered_tool(
            parameters_schema=[
                {"name": "optional_param", "type": "string", "label": "Optional"},
            ],
        )

        result = self.registry._to_tool_definition(tool)

        assert "required" not in result.parameters

    def test_default_value_included(self):
        tool = _make_registered_tool(
            parameters_schema=[
                {"name": "count", "type": "int", "label": "Count", "default_value": 10},
            ],
        )

        result = self.registry._to_tool_definition(tool)

        assert result.parameters["properties"]["count"]["default"] == 10

    def test_none_default_value_not_included(self):
        tool = _make_registered_tool(
            parameters_schema=[
                {"name": "count", "type": "int", "label": "Count", "default_value": None},
            ],
        )

        result = self.registry._to_tool_definition(tool)

        assert "default" not in result.parameters["properties"]["count"]

    def test_empty_parameters_schema(self):
        tool = _make_registered_tool(parameters_schema=[])

        result = self.registry._to_tool_definition(tool)

        assert result.parameters == {"type": "object", "properties": {}, "additionalProperties": False}

    def test_array_type_includes_items(self):
        tool = _make_registered_tool(
            parameters_schema=[
                {"name": "systems_involved", "type": "list", "label": "Systems Involved"},
            ],
        )

        result = self.registry._to_tool_definition(tool)

        prop = result.parameters["properties"]["systems_involved"]
        assert prop["type"] == "array"
        assert prop["items"] == {"type": "string"}

    def test_outer_schema_has_additional_properties_false(self):
        tool = _make_registered_tool(
            parameters_schema=[
                {"name": "ticket_id", "type": "int", "label": "Ticket ID", "required": True},
            ],
        )

        result = self.registry._to_tool_definition(tool)

        assert result.parameters["additionalProperties"] is False

    def test_object_type_has_additional_properties_true(self):
        tool = _make_registered_tool(
            parameters_schema=[
                {"name": "fields", "type": "dict", "label": "Fields"},
            ],
        )

        result = self.registry._to_tool_definition(tool)

        prop = result.parameters["properties"]["fields"]
        assert prop["type"] == "object"
        assert prop["additionalProperties"] is True

    def test_non_array_type_has_no_items(self):
        tool = _make_registered_tool(
            parameters_schema=[
                {"name": "name", "type": "string", "label": "Name"},
            ],
        )

        result = self.registry._to_tool_definition(tool)

        assert "items" not in result.parameters["properties"]["name"]

    def test_options_become_enum(self):
        tool = _make_registered_tool(
            parameters_schema=[
                {
                    "name": "status",
                    "type": "string",
                    "label": "Status",
                    "options": [
                        {"label": "Open", "value": "open"},
                        {"label": "Closed", "value": "closed"},
                    ],
                },
            ],
        )

        result = self.registry._to_tool_definition(tool)

        prop = result.parameters["properties"]["status"]
        assert prop["enum"] == ["open", "closed"]

    def test_no_options_means_no_enum(self):
        tool = _make_registered_tool(
            parameters_schema=[
                {"name": "name", "type": "string", "label": "Name"},
            ],
        )

        result = self.registry._to_tool_definition(tool)

        assert "enum" not in result.parameters["properties"]["name"]

    def test_label_falls_back_to_name(self):
        tool = _make_registered_tool(
            parameters_schema=[
                {"name": "ticket_id", "type": "int"},
            ],
        )

        result = self.registry._to_tool_definition(tool)

        assert result.parameters["properties"]["ticket_id"]["description"] == "ticket_id"

    def test_complete_json_schema_is_preserved_without_flattening(self):
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "filters": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ["open", "closed"],
                            }
                        },
                        "required": ["status"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["filters"],
            "additionalProperties": False,
        }
        tool = _make_registered_tool(parameters_schema=schema)

        result = self.registry._to_tool_definition(tool)

        assert result.parameters == schema
        assert result.parameters is not schema
        result.parameters["properties"]["filters"]["minItems"] = 2
        assert schema["properties"]["filters"]["minItems"] == 1


class TestWorkflowParametersToJsonSchema:
    def test_list_schema_preserves_options_defaults_and_container_shapes(self):
        result = workflow_parameters_to_json_schema(
            [
                {
                    "name": "status",
                    "type": "string",
                    "description": "Ticket status",
                    "options": [
                        {"label": "Open", "value": "open"},
                        {"label": "Closed", "value": "closed"},
                    ],
                    "default_value": "open",
                    "required": True,
                },
                {"name": "tags", "type": "list"},
                {"name": "metadata", "type": "dict"},
            ]
        )

        assert result == {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Ticket status",
                    "enum": ["open", "closed"],
                    "default": "open",
                },
                "tags": {
                    "type": "array",
                    "description": "tags",
                    "items": {"type": "string"},
                },
                "metadata": {
                    "type": "object",
                    "description": "metadata",
                    "additionalProperties": True,
                },
            },
            "additionalProperties": False,
            "required": ["status"],
        }


# ── format_tools_for_openai ───────────────────────────────────────────

class TestFormatToolsForOpenai:

    def test_formats_single_tool(self):
        tool = _make_tool_definition(
            name="wf_add_comment",
            description="Add a comment",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        )

        result = format_tools_for_openai([tool])

        assert len(result) == 1
        entry = result[0]
        assert entry["type"] == "function"
        assert entry["function"]["name"] == "wf_add_comment"
        assert entry["function"]["description"] == "Add a comment"
        assert entry["function"]["parameters"] == {
            "type": "object",
            "properties": {"text": {"type": "string"}},
        }

    def test_formats_multiple_tools(self):
        tools = [_make_tool_definition(name=f"tool_{i}") for i in range(3)]
        result = format_tools_for_openai(tools)
        assert len(result) == 3
        for i, entry in enumerate(result):
            assert entry["type"] == "function"
            assert entry["function"]["name"] == f"tool_{i}"

    def test_empty_list_returns_empty(self):
        assert format_tools_for_openai([]) == []


# ── format_tools_for_anthropic ────────────────────────────────────────

class TestFormatToolsForAnthropic:

    def test_formats_single_tool(self):
        tool = _make_tool_definition(
            name="halopsa_get_ticket",
            description="Get a ticket",
            parameters={"type": "object", "properties": {"id": {"type": "integer"}}},
        )

        result = format_tools_for_anthropic([tool])

        assert len(result) == 1
        entry = result[0]
        assert entry["name"] == "halopsa_get_ticket"
        assert entry["description"] == "Get a ticket"
        assert entry["input_schema"] == {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
        }

    def test_formats_multiple_tools(self):
        tools = [_make_tool_definition(name=f"tool_{i}") for i in range(3)]
        result = format_tools_for_anthropic(tools)
        assert len(result) == 3
        for i, entry in enumerate(result):
            assert entry["name"] == f"tool_{i}"

    def test_empty_list_returns_empty(self):
        assert format_tools_for_anthropic([]) == []

    def test_no_type_key_in_anthropic_format(self):
        tool = _make_tool_definition()
        result = format_tools_for_anthropic([tool])
        assert "type" not in result[0]

    def test_uses_input_schema_not_parameters(self):
        params = {"type": "object", "properties": {"x": {"type": "string"}}}
        tool = _make_tool_definition(parameters=params)
        result = format_tools_for_anthropic([tool])
        assert "input_schema" in result[0]
        assert "parameters" not in result[0]
        assert result[0]["input_schema"] is params


class TestToolRegistryQueries:
    @pytest.mark.asyncio
    async def test_get_all_tools_maps_active_tool_workflows(self):
        workflow = _workflow()
        session = _SequenceSession(_Result(scalars=[workflow]))
        registry = ToolRegistry(session=session)

        tools = await registry.get_all_tools()

        assert len(tools) == 1
        tool = tools[0]
        assert tool.id == workflow.id
        assert tool.name == "Get Ticket"
        assert tool.description == "Fetch ticket details"
        assert tool.category == "HaloPSA"
        assert tool.parameters_schema == workflow.parameters_schema
        assert tool.file_path == "workflows/get_ticket.py"
        assert tool.function_name == "run"
        assert len(session.statements) == 1

    @pytest.mark.asyncio
    async def test_get_tools_by_ids_returns_empty_without_query_for_empty_ids(self):
        session = _SequenceSession()
        registry = ToolRegistry(session=session)

        assert await registry.get_tools_by_ids([]) == []
        assert session.statements == []

    @pytest.mark.asyncio
    async def test_get_tools_by_ids_logs_assigned_non_tool_and_inactive_workflows(self, caplog):
        requested_id = uuid4()
        debug_rows = [
            type(
                "DebugWorkflow",
                (),
                {
                    "id": requested_id,
                    "name": "Regular Workflow",
                    "is_active": False,
                    "type": "workflow",
                },
            )()
        ]
        active_tool = _workflow(id=requested_id, tool_description=None)
        session = _SequenceSession(
            _Result(rows=debug_rows),
            _Result(scalars=[active_tool]),
        )
        registry = ToolRegistry(session=session)

        tools = await registry.get_tools_by_ids([requested_id])

        assert len(tools) == 1
        assert tools[0].description == "Fallback description"
        assert "type='workflow'" in caplog.text
        assert "is_active=False" in caplog.text
        assert len(session.statements) == 2

    @pytest.mark.asyncio
    async def test_get_tool_definitions_uses_all_tools_or_requested_ids(self, monkeypatch):
        registry = _make_registry()
        all_tool = _make_registered_tool(name="List Tickets", category="HaloPSA")
        requested_tool = _make_registered_tool(name="Search Knowledge", category="General")

        async def get_all_tools():
            return [all_tool]

        async def get_tools_by_ids(tool_ids):
            assert tool_ids == [requested_tool.id]
            return [requested_tool]

        monkeypatch.setattr(registry, "get_all_tools", get_all_tools)
        monkeypatch.setattr(registry, "get_tools_by_ids", get_tools_by_ids)

        assert [tool.name for tool in await registry.get_tool_definitions()] == [
            "halopsa_list_tickets"
        ]
        assert [
            tool.name for tool in await registry.get_tool_definitions([requested_tool.id])
        ] == ["wf_search_knowledge"]

    @pytest.mark.asyncio
    async def test_get_tool_by_name_and_id_return_none_for_missing_workflows(self):
        session = _SequenceSession(
            _Result(scalar=None),
            _Result(scalar=None),
        )
        registry = ToolRegistry(session=session)

        assert await registry.get_tool_by_name("Missing") is None
        assert await registry.get_tool_by_id(uuid4()) is None
        assert len(session.statements) == 2

    @pytest.mark.asyncio
    async def test_get_tool_by_name_and_id_map_workflow_rows(self):
        by_name = _workflow(name="Lookup Asset", tool_description=None)
        by_id = _workflow(name="Update Asset")
        session = _SequenceSession(
            _Result(scalar=by_name),
            _Result(scalar=by_id),
        )
        registry = ToolRegistry(session=session)

        name_tool = await registry.get_tool_by_name("Lookup Asset")
        id_tool = await registry.get_tool_by_id(by_id.id)

        assert name_tool is not None
        assert name_tool.description == "Fallback description"
        assert name_tool.file_path == by_name.path
        assert id_tool is not None
        assert id_tool.name == "Update Asset"
        assert id_tool.description == "Fetch ticket details"
        assert len(session.statements) == 2
