from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from src.models.enums import ExecutionStatus
from src.routers import workflows


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return self._value


class _Db:
    def __init__(self, *values):
        self.values = list(values)
        self.added = []
        self.committed = False

    async def execute(self, _stmt):
        if not self.values:
            raise AssertionError("unexpected execute call")
        return _ScalarResult(self.values.pop(0))

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.committed = True


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
        created_at=datetime.now(UTC),
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


@pytest.mark.asyncio
async def test_get_form_workflow_ids_collects_uuid_refs_and_skips_portable_refs():
    workflow_id = uuid4()
    launch_id = uuid4()
    provider_id = uuid4()
    form = SimpleNamespace(
        workflow_id=str(workflow_id),
        launch_workflow_id=str(launch_id),
        fields=[
            SimpleNamespace(data_provider_id=provider_id),
            SimpleNamespace(data_provider_id=None),
        ],
    )

    result = await workflows._get_form_workflow_ids(_Db(form), uuid4())

    assert result == {workflow_id, launch_id, provider_id}

    portable_form = SimpleNamespace(
        workflow_id="workflows/options.py::run",
        launch_workflow_id="not-a-uuid",
        fields=[],
    )

    assert await workflows._get_form_workflow_ids(_Db(portable_form), uuid4()) == set()
    assert await workflows._get_form_workflow_ids(_Db(None), uuid4()) == set()


@pytest.mark.asyncio
async def test_get_app_workflow_ids_resolves_file_dependencies_to_active_workflows():
    matched_id = uuid4()
    unmatched_id = uuid4()
    app = SimpleNamespace(repo_prefix="apps/customer_portal")
    file_rows = [
        ("const workflow = 'sync_records';",),
        (None,),
        (f"const workflowId = '{matched_id}';",),
    ]
    workflow_rows = [
        (matched_id, "other_name"),
        (unmatched_id, "unreferenced"),
    ]

    with patch(
        "src.services.app_dependencies.parse_dependencies",
        side_effect=[{"sync_records"}, {str(matched_id)}],
    ) as parse_dependencies:
        result = await workflows._get_app_workflow_ids(
            _Db(app, file_rows, workflow_rows),
            uuid4(),
        )

    assert result == {matched_id}
    assert parse_dependencies.call_count == 2
    assert await workflows._get_app_workflow_ids(_Db(None), uuid4()) == set()


@pytest.mark.asyncio
async def test_insert_scheduled_execution_persists_expected_execution_fields():
    workflow_id = uuid4()
    org_id = uuid4()
    executed_by = uuid4()
    form_id = uuid4()
    db = _Db()
    scheduled_at = datetime.now(UTC)

    execution_id = await workflows._insert_scheduled_execution(
        db=db,
        workflow_id=workflow_id,
        workflow_name="sync_records",
        parameters={"ticket": "123"},
        scheduled_at=scheduled_at,
        organization_id=org_id,
        executed_by=executed_by,
        executed_by_name="Ada",
        form_id=form_id,
        api_key_id=None,
        is_platform_admin=True,
    )

    assert isinstance(execution_id, UUID)
    assert db.committed is True
    assert len(db.added) == 1
    execution = db.added[0]
    assert execution.id == execution_id
    assert execution.workflow_id == workflow_id
    assert execution.workflow_name == "sync_records"
    assert execution.status == ExecutionStatus.SCHEDULED
    assert execution.parameters == {"ticket": "123"}
    assert execution.scheduled_at == scheduled_at
    assert execution.organization_id == org_id
    assert execution.executed_by == executed_by
    assert execution.executed_by_name == "Ada"
    assert execution.form_id == form_id
    assert execution.execution_context == {"is_platform_admin": True}
