from __future__ import annotations

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
