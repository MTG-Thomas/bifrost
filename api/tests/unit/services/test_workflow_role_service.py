from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from src.services.workflow_role_service import (
    WorkflowRoleService,
    extract_agent_workflow_ids,
    extract_app_workflow_ids,
    extract_form_workflow_ids,
    sync_agent_roles_to_workflows,
    sync_app_roles_to_workflows,
    sync_form_roles_to_workflows,
)


class ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return ScalarRows(self._rows)


def test_extract_form_workflow_ids_deduplicates_and_skips_portable_refs() -> None:
    main_id = uuid4()
    launch_id = uuid4()
    provider_id = uuid4()
    form = SimpleNamespace(
        workflow_id=str(main_id),
        launch_workflow_id="workflows/helpdesk.py::launch",
        fields=[
            SimpleNamespace(data_provider_id=provider_id),
            SimpleNamespace(data_provider_id=main_id),
            SimpleNamespace(data_provider_id=None),
        ],
    )

    result = extract_form_workflow_ids(form)

    assert set(result) == {main_id, provider_id}

    result_with_explicit_fields = extract_form_workflow_ids(
        SimpleNamespace(workflow_id=None, launch_workflow_id=str(launch_id), fields=[]),
        fields=[SimpleNamespace(data_provider_id=provider_id)],
    )
    assert set(result_with_explicit_fields) == {launch_id, provider_id}


def test_extract_agent_and_app_workflow_ids() -> None:
    tool_a = SimpleNamespace(id=uuid4())
    tool_b = SimpleNamespace(id=uuid4())

    assert extract_agent_workflow_ids(SimpleNamespace(tools=[tool_a, tool_b])) == [
        tool_a.id,
        tool_b.id,
    ]
    assert extract_app_workflow_ids() == []


@pytest.mark.asyncio
async def test_sync_entity_roles_to_workflows_inserts_cartesian_assignments() -> None:
    workflow_ids = [uuid4(), uuid4()]
    role_ids = [uuid4(), uuid4()]
    db = AsyncMock()

    await WorkflowRoleService(db).sync_entity_roles_to_workflows(
        workflow_ids,
        role_ids,
        assigned_by="operator@example.com",
    )

    db.execute.assert_awaited_once()
    statement = db.execute.await_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in compiled
    assert "DO NOTHING" in compiled


@pytest.mark.asyncio
async def test_sync_entity_roles_to_workflows_noops_for_empty_inputs() -> None:
    db = AsyncMock()
    service = WorkflowRoleService(db)

    await service.sync_entity_roles_to_workflows([], [uuid4()], assigned_by="system")
    await service.sync_entity_roles_to_workflows([uuid4()], [], assigned_by="system")

    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_role_lookup_methods_return_scalar_role_ids() -> None:
    form_roles = [uuid4()]
    agent_roles = [uuid4(), uuid4()]
    app_roles = [uuid4()]
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            Result(form_roles),
            Result(agent_roles),
            Result(app_roles),
        ]
    )
    service = WorkflowRoleService(db)

    assert await service.get_form_role_ids(uuid4()) == form_roles
    assert await service.get_agent_role_ids(uuid4()) == agent_roles
    assert await service.get_app_role_ids(uuid4()) == app_roles


@pytest.mark.asyncio
async def test_sync_form_roles_to_workflows_extracts_roles_and_assigns() -> None:
    workflow_id = uuid4()
    role_id = uuid4()
    form = SimpleNamespace(
        id=uuid4(),
        workflow_id=str(workflow_id),
        launch_workflow_id=None,
        fields=[],
    )

    with (
        patch.object(
            WorkflowRoleService,
            "get_form_role_ids",
            AsyncMock(return_value=[role_id]),
        ) as get_roles,
        patch.object(
            WorkflowRoleService,
            "sync_entity_roles_to_workflows",
            AsyncMock(),
        ) as sync_roles,
    ):
        await sync_form_roles_to_workflows(
            AsyncMock(),
            form,
            assigned_by="operator@example.com",
        )

    get_roles.assert_awaited_once_with(form.id)
    sync_roles.assert_awaited_once_with(
        workflow_ids=[workflow_id],
        role_ids=[role_id],
        assigned_by="operator@example.com",
    )


@pytest.mark.asyncio
async def test_sync_form_roles_to_workflows_stops_when_no_workflows_or_roles() -> None:
    form = SimpleNamespace(
        id=uuid4(),
        workflow_id=None,
        launch_workflow_id=None,
        fields=[],
    )

    with patch.object(
        WorkflowRoleService,
        "sync_entity_roles_to_workflows",
        AsyncMock(),
    ) as sync_roles:
        await sync_form_roles_to_workflows(AsyncMock(), form)

    sync_roles.assert_not_awaited()

    form.workflow_id = str(uuid4())
    with (
        patch.object(
            WorkflowRoleService,
            "get_form_role_ids",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            WorkflowRoleService,
            "sync_entity_roles_to_workflows",
            AsyncMock(),
        ) as sync_roles,
    ):
        await sync_form_roles_to_workflows(AsyncMock(), form)

    sync_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_agent_roles_to_workflows_assigns_agent_roles_to_tools() -> None:
    tool_id = uuid4()
    role_id = uuid4()
    agent = SimpleNamespace(id=uuid4(), tools=[SimpleNamespace(id=tool_id)])

    with (
        patch.object(
            WorkflowRoleService,
            "get_agent_role_ids",
            AsyncMock(return_value=[role_id]),
        ) as get_roles,
        patch.object(
            WorkflowRoleService,
            "sync_entity_roles_to_workflows",
            AsyncMock(),
        ) as sync_roles,
    ):
        await sync_agent_roles_to_workflows(
            AsyncMock(),
            agent,
            assigned_by="operator@example.com",
        )

    get_roles.assert_awaited_once_with(agent.id)
    sync_roles.assert_awaited_once_with(
        workflow_ids=[tool_id],
        role_ids=[role_id],
        assigned_by="operator@example.com",
    )


@pytest.mark.asyncio
async def test_sync_agent_roles_to_workflows_stops_when_no_tools_or_roles() -> None:
    agent = SimpleNamespace(id=uuid4(), tools=[])

    with patch.object(
        WorkflowRoleService,
        "sync_entity_roles_to_workflows",
        AsyncMock(),
    ) as sync_roles:
        await sync_agent_roles_to_workflows(AsyncMock(), agent)

    sync_roles.assert_not_awaited()

    agent.tools = [SimpleNamespace(id=uuid4())]
    with (
        patch.object(
            WorkflowRoleService,
            "get_agent_role_ids",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            WorkflowRoleService,
            "sync_entity_roles_to_workflows",
            AsyncMock(),
        ) as sync_roles,
    ):
        await sync_agent_roles_to_workflows(AsyncMock(), agent)

    sync_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_app_roles_to_workflows_is_compatibility_noop() -> None:
    db = AsyncMock()

    await sync_app_roles_to_workflows(
        db,
        uuid4(),
        assigned_by="operator@example.com",
    )

    db.execute.assert_not_called()
