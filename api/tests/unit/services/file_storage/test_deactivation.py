from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.file_storage.deactivation import DeactivationProtectionService


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _ExecuteResult:
    def __init__(self, values=None, scalar=None, rowcount=0):
        self._values = values or []
        self._scalar = scalar
        self.rowcount = rowcount

    def scalars(self):
        return _ScalarResult(self._values)

    def scalar_one_or_none(self):
        return self._scalar


def test_compute_similarity_rewards_shared_snake_case_parts() -> None:
    service = DeactivationProtectionService(db=None)

    renamed_score = service.compute_similarity("sync_customer_records", "sync_client_records")
    unrelated_score = service.compute_similarity("sync_customer_records", "archive_ticket")

    assert renamed_score > unrelated_score
    assert renamed_score > 0.6


@pytest.mark.asyncio
async def test_apply_workflow_replacements_ignores_invalid_ids() -> None:
    db = AsyncMock()
    service = DeactivationProtectionService(db)

    await service.apply_workflow_replacements({"not-a-uuid": "new_function"})

    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_apply_workflow_replacements_updates_valid_ids() -> None:
    db = AsyncMock()
    service = DeactivationProtectionService(db)
    workflow_id = uuid4()

    await service.apply_workflow_replacements({str(workflow_id): "new_function"})

    db.execute.assert_called_once()
    statement = db.execute.call_args.args[0]
    assert statement.compile().params["id_1"] == workflow_id
    assert statement.compile().params["function_name"] == "new_function"


@pytest.mark.asyncio
async def test_deactivate_workflows_by_id_returns_zero_without_valid_ids() -> None:
    db = AsyncMock()
    service = DeactivationProtectionService(db)

    assert await service.deactivate_workflows_by_id(["bad-id"]) == 0

    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_deactivate_workflows_by_id_updates_active_valid_ids() -> None:
    result = SimpleNamespace(rowcount=2)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    service = DeactivationProtectionService(db)
    first_id = uuid4()
    second_id = uuid4()

    count = await service.deactivate_workflows_by_id(
        [str(first_id), "bad-id", str(second_id)]
    )

    assert count == 2
    db.execute.assert_awaited_once()
    statement = db.execute.await_args.args[0]
    compiled = statement.compile()
    assert compiled.params["id_1"] == [first_id, second_id]
    assert compiled.params["is_active"] is False


@pytest.mark.asyncio
async def test_deactivate_workflows_by_id_returns_zero_when_update_matches_none() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0))
    service = DeactivationProtectionService(db)

    count = await service.deactivate_workflows_by_id([str(uuid4())])

    assert count == 0


@pytest.mark.asyncio
async def test_deactivate_removed_workflows_scopes_to_repo_rows_with_remaining_names() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=3))
    service = DeactivationProtectionService(db)

    count = await service.deactivate_removed_workflows(
        "workflows/sync.py",
        {"keep_customer_sync"},
    )

    assert count == 3
    statement = db.execute.await_args.args[0]
    compiled = statement.compile()
    assert compiled.params["path_1"] == "workflows/sync.py"
    assert compiled.params["function_name_1"] == ["keep_customer_sync"]
    assert compiled.params["is_active"] is False


@pytest.mark.asyncio
async def test_deactivate_removed_workflows_deactivates_all_repo_rows_when_empty() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    service = DeactivationProtectionService(db)

    count = await service.deactivate_removed_workflows("workflows/sync.py", set())

    assert count == 1
    statement = db.execute.await_args.args[0]
    compiled = statement.compile()
    assert compiled.params["path_1"] == "workflows/sync.py"
    assert "function_name_1" not in compiled.params
    assert compiled.params["is_active"] is False


@pytest.mark.asyncio
async def test_find_affected_entities_reports_forms_fields_and_agents() -> None:
    workflow_id = uuid4()
    form_id = uuid4()
    field_only_form_id = uuid4()
    agent_id = uuid4()
    form = SimpleNamespace(
        id=form_id,
        name="Intake",
        workflow_id=str(workflow_id),
        launch_workflow_id=str(workflow_id),
    )
    field = SimpleNamespace(form_id=field_only_form_id)
    field_form = SimpleNamespace(id=field_only_form_id, name="Field Form")
    agent = SimpleNamespace(id=agent_id, name="Ticket Agent")
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _ExecuteResult(values=[form]),
            _ExecuteResult(values=[field]),
            _ExecuteResult(scalar=field_form),
            _ExecuteResult(values=[agent]),
        ]
    )
    service = DeactivationProtectionService(db)

    affected = await service.find_affected_entities(str(workflow_id))

    assert affected == [
        {
            "entity_type": "form",
            "id": str(form_id),
            "name": "Intake",
            "reference_type": "workflow, launch_workflow",
        },
        {
            "entity_type": "form",
            "id": str(field_only_form_id),
            "name": "Field Form",
            "reference_type": "data_provider",
        },
        {
            "entity_type": "agent",
            "id": str(agent_id),
            "name": "Ticket Agent",
            "reference_type": "tool",
        },
    ]


@pytest.mark.asyncio
async def test_find_affected_entities_deduplicates_field_refs_for_existing_form() -> None:
    workflow_id = uuid4()
    form_id = uuid4()
    form = SimpleNamespace(
        id=form_id,
        name="Intake",
        workflow_id=str(workflow_id),
        launch_workflow_id=None,
    )
    field = SimpleNamespace(form_id=form_id)
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _ExecuteResult(values=[form]),
            _ExecuteResult(values=[field]),
            _ExecuteResult(values=[]),
        ]
    )
    service = DeactivationProtectionService(db)

    affected = await service.find_affected_entities(str(workflow_id))

    assert affected == [
        {
            "entity_type": "form",
            "id": str(form_id),
            "name": "Intake",
            "reference_type": "workflow",
        }
    ]
    assert db.execute.await_count == 3


@pytest.mark.asyncio
async def test_detect_pending_deactivations_includes_execution_and_replacements() -> None:
    removed_id = uuid4()
    retained_id = uuid4()
    removed = SimpleNamespace(
        id=removed_id,
        name="Sync Customers",
        function_name="sync_customer_records",
        path="workflows/sync.py",
        description="syncs customer data",
        type="workflow",
        endpoint_enabled=True,
    )
    retained = SimpleNamespace(
        id=retained_id,
        name="Keep Customers",
        function_name="keep_customer_records",
        path="workflows/sync.py",
        description=None,
        type=None,
        endpoint_enabled=False,
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _ExecuteResult(values=[removed, retained]),
            _ExecuteResult(
                scalar=SimpleNamespace(
                    started_at=SimpleNamespace(
                        isoformat=lambda: "2026-07-05T10:00:00+00:00"
                    )
                )
            ),
        ]
    )
    service = DeactivationProtectionService(db)
    service.find_affected_entities = AsyncMock(
        return_value=[
            {
                "entity_type": "form",
                "id": "form-1",
                "name": "Intake",
                "reference_type": "workflow",
            }
        ]
    )

    pending, replacements = await service.detect_pending_deactivations(
        "workflows/sync.py",
        {"keep_customer_records", "sync_client_records", "archive_ticket"},
        {"sync_client_records": ("workflow", "Sync Clients")},
    )

    assert len(pending) == 1
    assert pending[0].id == str(removed_id)
    assert pending[0].function_name == "sync_customer_records"
    assert pending[0].has_executions is True
    assert pending[0].last_execution_at == "2026-07-05T10:00:00+00:00"
    assert pending[0].endpoint_enabled is True
    assert pending[0].affected_entities[0]["name"] == "Intake"
    assert replacements[0].function_name == "sync_client_records"
    assert replacements[0].name == "Sync Clients"
    assert replacements[0].decorator_type == "workflow"
    assert replacements[0].similarity_score > 0.6
    assert all(r.function_name != "archive_ticket" for r in replacements)
    service.find_affected_entities.assert_awaited_once_with(str(removed_id))


@pytest.mark.asyncio
async def test_detect_pending_deactivations_returns_empty_when_nothing_removed() -> None:
    existing = SimpleNamespace(
        id=uuid4(),
        name="Sync Customers",
        function_name="sync_customer_records",
        path="workflows/sync.py",
        description=None,
        type="workflow",
        endpoint_enabled=False,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_ExecuteResult(values=[existing]))
    service = DeactivationProtectionService(db)
    service.find_affected_entities = AsyncMock()

    pending, replacements = await service.detect_pending_deactivations(
        "workflows/sync.py",
        {"sync_customer_records"},
        {},
    )

    assert pending == []
    assert replacements == []
    service.find_affected_entities.assert_not_called()
