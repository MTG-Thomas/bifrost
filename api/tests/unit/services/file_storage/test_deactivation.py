from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.file_storage.deactivation import DeactivationProtectionService


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
