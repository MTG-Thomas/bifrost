from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.file_storage.indexers.form import FormIndexer


@pytest.mark.asyncio
async def test_index_form_rejects_invalid_yaml_without_db_write() -> None:
    db = AsyncMock()

    assert await FormIndexer(db).index_form("forms/bad.form.yaml", b"name: [") is False

    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_index_form_requires_name_without_db_write() -> None:
    db = AsyncMock()

    assert (
        await FormIndexer(db).index_form(
            "forms/missing-name.form.yaml",
            b"description: No name\n",
        )
        is False
    )

    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_delete_form_for_file_rejects_paths_without_form_uuid() -> None:
    db = AsyncMock()

    assert await FormIndexer(db).delete_form_for_file("forms/not-a-form.txt") == 0

    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_delete_form_for_file_deletes_by_uuid() -> None:
    form_id = uuid4()
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(rowcount=1)

    count = await FormIndexer(db).delete_form_for_file(f"forms/{form_id}.form.yaml")

    assert count == 1
    statement = db.execute.call_args.args[0]
    assert statement.compile().params["id_1"] == form_id


@pytest.mark.asyncio
async def test_index_form_normalizes_flat_fields_and_resolves_legacy_workflows(
    monkeypatch,
) -> None:
    form_id = uuid4()
    workflow_id = uuid4()
    launch_workflow_id = uuid4()
    db = AsyncMock()
    added_fields = []
    db.add.side_effect = added_fields.append

    indexer = FormIndexer(db)
    resolved_names = []

    async def resolve(name: str) -> str:
        resolved_names.append(name)
        return str(workflow_id if name == "Primary Workflow" else launch_workflow_id)

    monkeypatch.setattr(indexer, "resolve_workflow_name_to_id", resolve)

    modified = await indexer.index_form(
        f"forms/{form_id}.form.yaml",
        f"""
id: {form_id}
name: Intake
linked_workflow: Primary Workflow
launch_workflow: Launch Workflow
fields:
  - name: customer
    label: Customer
    type: text
    required: true
    default: Midtown
  - label: Missing name is ignored
""".encode(),
    )

    assert modified is False
    assert resolved_names == ["Primary Workflow", "Launch Workflow"]
    assert db.execute.call_count == 2
    assert len(added_fields) == 1
    assert added_fields[0].form_id == form_id
    assert added_fields[0].name == "customer"
    assert added_fields[0].default_value == "Midtown"
