from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.routers import workflows


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Db:
    def __init__(self, *values):
        self.values = list(values)

    async def execute(self, _stmt):
        if not self.values:
            raise AssertionError("unexpected execute call")
        return _ScalarResult(self.values.pop(0))


def test_extract_workflows_from_props_recurses_lists_and_dicts():
    workflow_id = str(uuid4())
    provider_id = str(uuid4())
    nested_id = str(uuid4())
    values: set[str] = set()

    workflows._extract_workflows_from_props(
        {
            "workflowId": workflow_id,
            "children": [
                {"dataProviderId": provider_id},
                {"props": {"onClick": {"workflowId": nested_id}}},
                "ignored",
            ],
            "empty": None,
        },
        values,
    )

    assert values == {workflow_id, provider_id, nested_id}


def test_convert_workflow_orm_to_schema_normalizes_defaults():
    workflow_id = uuid4()
    solution_id = uuid4()
    row = SimpleNamespace(
        id=workflow_id,
        name="sync",
        function_name="run",
        display_name="Sync Records",
        description="",
        category=None,
        tags=None,
        type="tool",
        organization_id=None,
        solution_id=solution_id,
        access_level=None,
        parameters_schema=[
            {"name": "ticket_id", "type": "string", "required": True}
        ],
        execution_mode="invalid",
        timeout_seconds=None,
        endpoint_enabled=None,
        allowed_methods=None,
        disable_global_key=None,
        public_endpoint=None,
        tool_description="Tool docs",
        cache_ttl_seconds=None,
        time_saved=None,
        value=None,
        path="workflows/sync.py",
        created_at=None,
    )

    result = workflows._convert_workflow_orm_to_schema(row, used_by_count=3)

    assert result.id == str(workflow_id)
    assert result.category == "General"
    assert result.description is None
    assert result.execution_mode == "sync"
    assert result.timeout_seconds == 1800
    assert result.allowed_methods == ["POST"]
    assert result.is_tool is True
    assert result.is_solution_managed is True
    assert result.solution_id == solution_id
    assert result.parameters[0].name == "ticket_id"
    assert result.used_by_count == 3


@pytest.mark.asyncio
async def test_derive_solution_scope_prefers_valid_explicit_solution_id():
    solution_id = uuid4()

    result = await workflows._derive_solution_scope(
        _Db(),
        solution_id=str(solution_id),
        form_id=str(uuid4()),
        app_id=str(uuid4()),
    )

    assert result == solution_id


@pytest.mark.asyncio
async def test_derive_solution_scope_returns_none_for_bad_ids():
    assert await workflows._derive_solution_scope(
        _Db(),
        solution_id="not-a-uuid",
        form_id=None,
        app_id=None,
    ) is None

    assert await workflows._derive_solution_scope(
        _Db(),
        solution_id=None,
        form_id="not-a-uuid",
        app_id=None,
    ) is None

    assert await workflows._derive_solution_scope(
        _Db(),
        solution_id=None,
        form_id=None,
        app_id="not-a-uuid",
    ) is None


@pytest.mark.asyncio
async def test_derive_solution_scope_uses_form_then_app_lookup():
    form_solution = uuid4()
    app_solution = uuid4()

    assert await workflows._derive_solution_scope(
        _Db(form_solution),
        solution_id=None,
        form_id=str(uuid4()),
        app_id=None,
    ) == form_solution

    assert await workflows._derive_solution_scope(
        _Db(app_solution),
        solution_id=None,
        form_id=None,
        app_id=str(uuid4()),
    ) == app_solution

    assert await workflows._derive_solution_scope(
        _Db(),
        solution_id=None,
        form_id=None,
        app_id=None,
    ) is None

