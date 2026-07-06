from __future__ import annotations

import ast
import sys
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import Update

from src.services.file_storage.indexers.workflow import WorkflowIndexer


@pytest.mark.asyncio
async def test_extract_metadata_requires_real_sdk_decorator() -> None:
    indexer = WorkflowIndexer(db=None)

    assert await indexer.extract_metadata("plain.py", b"# @workflow in comment") is None

    content = b"""
from src.sdk.decorators import data_provider

@data_provider(name="Regions")
async def regions():
    return []
"""

    assert await indexer.extract_metadata("providers.py", content) == {
        "has_decorators": True
    }


@pytest.mark.asyncio
async def test_extract_metadata_detects_tool_only_file() -> None:
    indexer = WorkflowIndexer(db=None)

    content = b"""
from bifrost import tool

@tool(name="Lookup ticket")
def lookup_ticket(ticket_id: str):
    return {"ticket_id": ticket_id}
"""

    assert await indexer.extract_metadata("tools.py", content) == {
        "has_decorators": True
    }


@pytest.mark.asyncio
async def test_extract_metadata_ignores_plain_and_invalid_python_files() -> None:
    indexer = WorkflowIndexer(db=None)

    assert await indexer.extract_metadata("plain.py", b"def helper():\n    pass\n") is None
    assert await indexer.extract_metadata("broken.py", b"@workflow\ndef broken(:\n") is None


@pytest.mark.asyncio
async def test_extract_metadata_tolerates_decode_errors() -> None:
    class Undecodable:
        def decode(self, *_args, **_kwargs):
            raise UnicodeError("bad bytes")

    indexer = WorkflowIndexer(db=None)

    assert await indexer.extract_metadata("bad.py", Undecodable()) is None


def test_parse_decorator_extracts_supported_keyword_values() -> None:
    indexer = WorkflowIndexer(db=None)
    decorator = ast.parse(
        '@bifrost.workflow(name="Daily", tags=["ops", "sync"], config={"retries": 2})\n'
        "def run():\n"
        "    pass\n"
    ).body[0].decorator_list[0]

    assert indexer._parse_decorator(decorator) == (
        "workflow",
        {
            "name": "Daily",
            "tags": ["ops", "sync"],
            "config": {"retries": 2},
        },
    )


def test_parse_decorator_handles_bare_names_and_rejects_non_sdk_decorators() -> None:
    indexer = WorkflowIndexer(db=None)
    module = ast.parse(
        "@workflow\n"
        "def registered():\n"
        "    pass\n\n"
        "@staticmethod\n"
        "def helper():\n"
        "    pass\n\n"
        "@factory.workflow()\n"
        "def nested():\n"
        "    pass\n"
    )

    assert indexer._parse_decorator(module.body[0].decorator_list[0]) == (
        "workflow",
        {},
    )
    assert indexer._parse_decorator(module.body[1].decorator_list[0]) is None
    assert indexer._parse_decorator(module.body[2].decorator_list[0]) == (
        "workflow",
        {},
    )
    assert indexer._parse_decorator(ast.Constant(value="workflow")) is None

    lambda_decorator = ast.parse(
        "@(lambda fn: fn)()\n"
        "def generated():\n"
        "    pass\n"
    ).body[0].decorator_list[0]
    assert indexer._parse_decorator(lambda_decorator) is None

    ignored_call = ast.parse(
        "@not_workflow()\n"
        "def generated():\n"
        "    pass\n"
    ).body[0].decorator_list[0]
    assert indexer._parse_decorator(ignored_call) is None


def test_ast_value_to_python_handles_simple_literals_and_rejects_expressions() -> None:
    indexer = WorkflowIndexer(db=None)
    assignments = ast.parse(
        "truthy = True\n"
        "falsy = False\n"
        "missing = None\n"
        "computed = 1 + 2\n"
    ).body

    assert indexer._ast_value_to_python(assignments[0].value) is True
    assert indexer._ast_value_to_python(assignments[1].value) is False
    assert indexer._ast_value_to_python(assignments[2].value) is None
    assert indexer._ast_value_to_python(assignments[3].value) is None
    assert indexer._ast_value_to_python(ast.Name(id="True")) is True
    assert indexer._ast_value_to_python(ast.Name(id="False")) is False
    assert indexer._ast_value_to_python(ast.Name(id="None")) is None


def test_extract_parameters_maps_annotations_defaults_and_literals() -> None:
    indexer = WorkflowIndexer(db=None)
    func = ast.parse(
        "from typing import Literal, Optional\n"
        "def run(context, count: int, enabled: bool = True, "
        "mode: Literal['fast', 'safe'] = 'fast', note: str | None = None, "
        "payload: dict[str, str] = {}):\n"
        "    pass\n"
    ).body[1]

    parameters = indexer._extract_parameters_from_ast(func)

    assert parameters == [
        {"name": "count", "type": "int", "required": True, "label": "Count"},
        {
            "name": "enabled",
            "type": "bool",
            "required": False,
            "label": "Enabled",
            "default_value": True,
        },
        {
            "name": "mode",
            "type": "string",
            "required": False,
            "label": "Mode",
            "default_value": "fast",
            "options": [
                {"label": "fast", "value": "fast"},
                {"label": "safe", "value": "safe"},
            ],
        },
        {"name": "note", "type": "string", "required": False, "label": "Note"},
        {
            "name": "payload",
            "type": "json",
            "required": False,
            "label": "Payload",
            "default_value": {},
        },
    ]


def test_execution_context_annotation_is_not_exposed_as_user_parameter() -> None:
    indexer = WorkflowIndexer(db=None)
    func = ast.parse(
        "def run(ctx: ExecutionContext, customer_name: str):\n"
        "    pass\n"
    ).body[0]

    assert indexer._extract_parameters_from_ast(func) == [
        {
            "name": "customer_name",
            "type": "string",
            "required": True,
            "label": "Customer Name",
        }
    ]


def test_extract_parameters_maps_list_optional_and_numeric_literal_types() -> None:
    indexer = WorkflowIndexer(db=None)
    func = ast.parse(
        "from typing import Literal, Optional\n"
        "def run(items: list[str], amount: Optional[float], "
        "level: Literal[1, 2] = 1, ratio: Literal[1.5] = 1.5, "
        "enabled: Literal[True] = True, unknown: object = None):\n"
        "    pass\n"
    ).body[1]

    assert indexer._extract_parameters_from_ast(func) == [
        {
            "name": "items",
            "type": "list",
            "required": True,
            "label": "Items",
        },
        {
            "name": "amount",
            "type": "float",
            "required": False,
            "label": "Amount",
        },
        {
            "name": "level",
            "type": "int",
            "required": False,
            "label": "Level",
            "default_value": 1,
            "options": [
                {"label": "1", "value": "1"},
                {"label": "2", "value": "2"},
            ],
        },
        {
            "name": "ratio",
            "type": "float",
            "required": False,
            "label": "Ratio",
            "default_value": 1.5,
            "options": [{"label": "1.5", "value": "1.5"}],
        },
        {
            "name": "enabled",
            "type": "bool",
            "required": False,
            "label": "Enabled",
            "default_value": True,
            "options": [{"label": "True", "value": "True"}],
        },
        {
            "name": "unknown",
            "type": "json",
            "required": False,
            "label": "Unknown",
        },
    ]


def test_annotation_helpers_cover_attributes_and_non_literal_options() -> None:
    indexer = WorkflowIndexer(db=None)
    annotations = ast.parse(
        "from typing import Literal, Optional\n"
        "def run(ctx: sdk.ExecutionContext, single: Literal[None], "
        "empty: Literal[()], optional_list: Optional[list[str]], "
        "unioned: None | str, plain: object):\n"
        "    pass\n"
    ).body[1].args.args

    assert indexer._annotation_to_string(annotations[0].annotation) == "sdk.ExecutionContext"
    assert indexer._annotation_to_string(ast.Tuple(elts=[])) == ""
    assert indexer._annotation_to_ui_type(annotations[3].annotation) == "string"
    assert indexer._annotation_to_ui_type(ast.Tuple(elts=[])) == "json"
    assert indexer._infer_literal_type(annotations[1].annotation.slice) == "string"
    assert indexer._infer_literal_type(ast.Tuple(elts=[])) == "string"
    assert indexer._infer_literal_type(ast.List(elts=[])) == "string"
    assert indexer._extract_literal_options(annotations[5].annotation) is None
    assert indexer._extract_literal_options(ast.Attribute(value=ast.Name(id="typing"), attr="Literal")) is None
    assert indexer._extract_literal_options(
        ast.Subscript(
            value=ast.Attribute(value=ast.Name(id="typing"), attr="Literal"),
            slice=ast.Constant(value="x"),
        )
    ) is None
    assert indexer._is_optional_annotation(annotations[4].annotation) is True


@pytest.mark.asyncio
async def test_index_python_file_initializes_missing_fields_without_overwriting_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = uuid4()
    existing = SimpleNamespace(
        id=workflow_id,
        is_active=False,
        name=None,
        description=None,
        category="General",
        tags=[],
        endpoint_enabled=True,
        path="workflows/tickets.py",
        timeout_seconds=900,
        time_saved=12,
        value=50,
        execution_mode="async",
    )
    refreshed = SimpleNamespace(**vars(existing))
    refreshed.name = "Existing Ticket Sync"

    db = AsyncMock()
    refetch = MagicMock()
    refetch.scalar_one.return_value = refreshed
    db.execute.side_effect = [MagicMock(), refetch]

    redis_client = AsyncMock()
    monkeypatch.setattr(
        "src.core.redis_client.get_redis_client",
        lambda: redis_client,
    )

    indexer = WorkflowIndexer(db)
    indexer.refresh_workflow_endpoint = AsyncMock()
    indexer.set_prefetch_cache({("workflows/tickets.py", "sync_tickets"): existing})

    content = b'''
from typing import Literal
from bifrost import workflow

@workflow(
    name="Ticket Sync",
    description="Pull updated tickets",
    category="Operations",
    tags=["psa", "sync"],
    timeout_seconds=1,
)
def sync_tickets(status: Literal["open", "closed"], limit: int = 100):
    """Docstring fallback should not be needed."""
    return []
'''

    await indexer.index_python_file("workflows/tickets.py", content)

    update_stmt = db.execute.call_args_list[0][0][0]
    assert isinstance(update_stmt, Update)
    params = update_stmt.compile().params
    assert params["id_1"] == workflow_id
    assert params["name"] == "Ticket Sync"
    assert params["description"] == "Pull updated tickets"
    assert params["category"] == "Operations"
    assert params["tags"] == ["psa", "sync"]
    assert params["is_active"] is True
    assert params["is_orphaned"] is False
    assert params["type"] == "workflow"
    assert params["parameters_schema"] == [
        {
            "name": "status",
            "type": "string",
            "required": True,
            "label": "Status",
            "options": [
                {"label": "open", "value": "open"},
                {"label": "closed", "value": "closed"},
            ],
        },
        {
            "name": "limit",
            "type": "int",
            "required": False,
            "label": "Limit",
            "default_value": 100,
        },
    ]
    assert "timeout_seconds" not in params

    indexer.refresh_workflow_endpoint.assert_awaited_once_with(refreshed)
    redis_client.invalidate_endpoint_workflow_cache.assert_awaited_once_with(
        str(workflow_id)
    )
    redis_client.set_workflow_metadata_cache.assert_awaited_once_with(
        workflow_id=str(workflow_id),
        name="Existing Ticket Sync",
        file_path="workflows/tickets.py",
        timeout_seconds=900,
        time_saved=12,
        value=50,
        execution_mode="async",
    )


@pytest.mark.asyncio
async def test_index_python_file_preserves_curated_fields_and_marks_tools() -> None:
    workflow_id = uuid4()
    existing = SimpleNamespace(
        id=workflow_id,
        is_active=True,
        name="Curated name",
        description="Curated description",
        category="Curated",
        tags=["kept"],
        endpoint_enabled=False,
        path="tools/customer.py",
        timeout_seconds=1800,
        time_saved=0,
        value=0,
        execution_mode="sync",
    )

    db = AsyncMock()
    refetch = MagicMock()
    refetch.scalar_one.return_value = existing
    db.execute.side_effect = [MagicMock(), refetch]

    indexer = WorkflowIndexer(db)
    indexer.set_prefetch_cache({("tools/customer.py", "lookup_customer"): existing})

    await indexer.index_python_file(
        "tools/customer.py",
        b'''
from bifrost import tool

@tool(name="Generated name", description="Generated description", category="Generated", tags=["new"])
def lookup_customer(customer_id: str):
    return {}
''',
    )

    update_stmt = db.execute.call_args_list[0][0][0]
    params = update_stmt.compile().params
    assert params["type"] == "tool"
    assert "name" not in params
    assert "description" not in params
    assert "category" not in params
    assert "tags" not in params


@pytest.mark.asyncio
async def test_index_python_file_queries_database_and_skips_unregistered_functions() -> None:
    db = AsyncMock()
    lookup = MagicMock()
    lookup.scalar_one_or_none.return_value = None
    db.execute.return_value = lookup

    indexer = WorkflowIndexer(db)

    await indexer.index_python_file(
        "workflows/unregistered.py",
        b'''
from bifrost import workflow, data_provider

@workflow
def missing_workflow(customer_id: str):
    return None

@data_provider
def missing_provider():
    return []
''',
    )

    assert db.execute.call_count == 2
    for call in db.execute.call_args_list:
        compiled = call[0][0].compile()
        params = compiled.params
        assert params["path_1"] == "workflows/unregistered.py"
        assert "solution_id IS NULL" in str(compiled)


@pytest.mark.asyncio
async def test_index_python_file_returns_without_updates_for_syntax_errors() -> None:
    db = AsyncMock()
    indexer = WorkflowIndexer(db)

    await indexer.index_python_file("workflows/broken.py", b"@workflow\ndef broken(:\n")

    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_index_python_file_logs_cache_failures_without_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = uuid4()
    existing = SimpleNamespace(
        id=workflow_id,
        is_active=True,
        name="Ticket Sync",
        description="Pull tickets",
        category="Operations",
        tags=["psa"],
        endpoint_enabled=False,
        path="workflows/tickets.py",
        timeout_seconds=900,
        time_saved=12,
        value=50,
        execution_mode="async",
    )

    db = AsyncMock()
    refetch = MagicMock()
    refetch.scalar_one.return_value = existing
    db.execute.side_effect = [MagicMock(), refetch]

    class FailingRedis:
        async def invalidate_endpoint_workflow_cache(self, _workflow_id: str) -> None:
            raise RuntimeError("redis unavailable")

    monkeypatch.setattr(
        "src.core.redis_client.get_redis_client",
        lambda: FailingRedis(),
    )

    indexer = WorkflowIndexer(db)
    indexer.set_prefetch_cache({("workflows/tickets.py", "sync_tickets"): existing})

    await indexer.index_python_file(
        "workflows/tickets.py",
        b"""
from bifrost import workflow

@workflow
def sync_tickets():
    return []
""",
    )

    assert db.execute.call_count == 2


@pytest.mark.asyncio
async def test_index_python_file_updates_registered_data_provider() -> None:
    provider_id = uuid4()
    existing = SimpleNamespace(
        id=provider_id,
        is_active=False,
        name=None,
        description=None,
        category=None,
        tags=[],
    )

    db = AsyncMock()
    db.execute.return_value = MagicMock()

    indexer = WorkflowIndexer(db)
    indexer.set_prefetch_cache({("providers/regions.py", "regions"): existing})

    await indexer.index_python_file(
        "providers/regions.py",
        b'''
from bifrost import data_provider

@data_provider(name="Regions", description="Available regions", category="Forms", tags=["geo"])
async def regions(country: str | None = None):
    return []
''',
    )

    update_stmt = db.execute.call_args_list[0][0][0]
    params = update_stmt.compile().params
    assert params["id_1"] == provider_id
    assert params["type"] == "data_provider"
    assert params["is_active"] is True
    assert params["is_orphaned"] is False
    assert params["name"] == "Regions"
    assert params["description"] == "Available regions"
    assert params["category"] == "Forms"
    assert params["tags"] == ["geo"]
    assert params["parameters_schema"] == [
        {
            "name": "country",
            "type": "string",
            "required": False,
            "label": "Country",
        }
    ]


@pytest.mark.asyncio
async def test_refresh_workflow_endpoint_calls_dynamic_endpoint_refresher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    openapi_module = ModuleType("src.services.openapi_endpoints")
    main_module = ModuleType("src.main")
    main_module.app = object()

    def refresh_endpoint(app, workflow):
        calls.append((app, workflow))

    openapi_module.refresh_workflow_endpoint = refresh_endpoint
    monkeypatch.setitem(sys.modules, "src.services.openapi_endpoints", openapi_module)
    monkeypatch.setitem(sys.modules, "src.main", main_module)

    workflow = SimpleNamespace(name="Ticket Sync")
    indexer = WorkflowIndexer(db=None)

    await indexer.refresh_workflow_endpoint(workflow)

    assert calls == [(main_module.app, workflow)]


@pytest.mark.asyncio
async def test_refresh_workflow_endpoint_tolerates_unavailable_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    indexer = WorkflowIndexer(db=None)

    def raise_import_error(*_args, **_kwargs):
        raise ImportError("startup still loading")

    monkeypatch.setattr(
        "builtins.__import__",
        raise_import_error,
    )

    await indexer.refresh_workflow_endpoint(SimpleNamespace(name="Deferred endpoint"))


@pytest.mark.asyncio
async def test_refresh_workflow_endpoint_logs_refresher_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openapi_module = ModuleType("src.services.openapi_endpoints")
    main_module = ModuleType("src.main")
    main_module.app = object()

    def refresh_endpoint(_app, _workflow):
        raise RuntimeError("route rebuild failed")

    openapi_module.refresh_workflow_endpoint = refresh_endpoint
    monkeypatch.setitem(sys.modules, "src.services.openapi_endpoints", openapi_module)
    monkeypatch.setitem(sys.modules, "src.main", main_module)

    indexer = WorkflowIndexer(db=None)

    await indexer.refresh_workflow_endpoint(SimpleNamespace(name="Ticket Sync"))


@pytest.mark.asyncio
async def test_delete_workflows_for_file_marks_only_matching_workspace_rows() -> None:
    db = AsyncMock()
    result = MagicMock()
    result.rowcount = 3
    db.execute.return_value = result

    count = await WorkflowIndexer(db).delete_workflows_for_file("workflows/old.py")

    assert count == 3
    stmt = db.execute.call_args[0][0]
    compiled = stmt.compile()
    params = compiled.params
    assert params["path_1"] == "workflows/old.py"
    assert "solution_id IS NULL" in str(compiled)
    assert params["is_active"] is False
    assert params["is_orphaned"] is True


@pytest.mark.asyncio
async def test_delete_workflows_for_file_returns_zero_for_empty_rowcount() -> None:
    db = AsyncMock()
    result = MagicMock()
    result.rowcount = None
    db.execute.return_value = result

    assert await WorkflowIndexer(db).delete_workflows_for_file("workflows/old.py") == 0
