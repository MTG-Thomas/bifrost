from __future__ import annotations

import ast

import pytest

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
