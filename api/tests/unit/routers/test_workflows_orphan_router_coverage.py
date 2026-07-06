from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.github import ReplaceWorkflowRequest
from src.routers import workflows


def _orphan(**overrides):
    row = SimpleNamespace(
        id=str(uuid4()),
        name="Missing workflow",
        function_name="run",
        last_path="workflows/missing.py",
        code="def run(): pass",
        used_by=[
            SimpleNamespace(type="form", id=str(uuid4()), name="Intake"),
            SimpleNamespace(type="app", id=str(uuid4()), name="Portal"),
        ],
        orphaned_at=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _replacement(path: str, function_name: str, compatibility: str):
    return SimpleNamespace(
        path=path,
        function_name=function_name,
        signature=f"{function_name}(payload)",
        compatibility=compatibility,
    )


@pytest.mark.asyncio
async def test_list_orphaned_workflows_maps_service_rows():
    service = SimpleNamespace(
        get_orphaned_workflows=AsyncMock(return_value=[_orphan()])
    )

    with patch(
        "src.services.workflow_orphan.WorkflowOrphanService",
        return_value=service,
    ):
        result = await workflows.list_orphaned_workflows(
            ctx=None,
            user=SimpleNamespace(email="admin@example.com"),
            db=object(),
        )

    assert len(result.workflows) == 1
    orphan = result.workflows[0]
    assert orphan.name == "Missing workflow"
    assert [ref.type for ref in orphan.used_by] == ["form", "app"]
    service.get_orphaned_workflows.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_orphaned_workflows_translates_service_errors():
    service = SimpleNamespace(
        get_orphaned_workflows=AsyncMock(side_effect=RuntimeError("db down"))
    )

    with patch(
        "src.services.workflow_orphan.WorkflowOrphanService",
        return_value=service,
    ):
        with pytest.raises(HTTPException) as exc:
            await workflows.list_orphaned_workflows(
                ctx=None,
                user=SimpleNamespace(email="admin@example.com"),
                db=object(),
            )

    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to list orphaned workflows"


@pytest.mark.asyncio
async def test_get_compatible_replacements_filters_incompatible_results():
    workflow_id = uuid4()
    service = SimpleNamespace(
        get_compatible_replacements=AsyncMock(
            return_value=[
                _replacement("workflows/exact.py", "run", "exact"),
                _replacement("workflows/compatible.py", "handle", "compatible"),
                _replacement("workflows/bad.py", "bad", "incompatible"),
            ]
        )
    )

    with patch(
        "src.services.workflow_orphan.WorkflowOrphanService",
        return_value=service,
    ):
        result = await workflows.get_compatible_replacements(
            workflow_id,
            ctx=None,
            user=SimpleNamespace(email="admin@example.com"),
            db=object(),
        )

    assert [item.path for item in result.replacements] == [
        "workflows/exact.py",
        "workflows/compatible.py",
    ]
    service.get_compatible_replacements.assert_awaited_once_with(workflow_id)


@pytest.mark.asyncio
async def test_get_compatible_replacements_translates_value_errors():
    service = SimpleNamespace(
        get_compatible_replacements=AsyncMock(side_effect=ValueError("not orphaned"))
    )

    with patch(
        "src.services.workflow_orphan.WorkflowOrphanService",
        return_value=service,
    ):
        with pytest.raises(HTTPException) as exc:
            await workflows.get_compatible_replacements(
                uuid4(),
                ctx=None,
                user=SimpleNamespace(email="admin@example.com"),
                db=object(),
            )

    assert exc.value.status_code == 400
    assert exc.value.detail == "not orphaned"


@pytest.mark.asyncio
async def test_replace_workflow_checks_solution_guard_and_returns_new_path():
    workflow_id = uuid4()
    replaced = SimpleNamespace(id=workflow_id, path="workflows/new.py")
    service = SimpleNamespace(replace_workflow=AsyncMock(return_value=replaced))

    with (
        patch.object(
            workflows,
            "assert_entity_id_not_solution_managed",
            new=AsyncMock(),
        ) as guard,
        patch(
            "src.services.workflow_orphan.WorkflowOrphanService",
            return_value=service,
        ),
    ):
        result = await workflows.replace_workflow(
            workflow_id,
            ReplaceWorkflowRequest(
                source_path="workflows/new.py",
                function_name="run",
                allow_type_change=True,
            ),
            ctx=None,
            user=SimpleNamespace(email="admin@example.com"),
            db=object(),
        )

    guard.assert_awaited_once()
    service.replace_workflow.assert_awaited_once_with(
        workflow_id=workflow_id,
        source_path="workflows/new.py",
        function_name="run",
        allow_type_change=True,
    )
    assert result.success is True
    assert result.workflow_id == str(workflow_id)
    assert result.new_path == "workflows/new.py"


@pytest.mark.asyncio
async def test_replace_workflow_translates_service_value_errors():
    service = SimpleNamespace(
        replace_workflow=AsyncMock(side_effect=ValueError("signature mismatch"))
    )

    with (
        patch.object(
            workflows,
            "assert_entity_id_not_solution_managed",
            new=AsyncMock(),
        ),
        patch(
            "src.services.workflow_orphan.WorkflowOrphanService",
            return_value=service,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await workflows.replace_workflow(
                uuid4(),
                ReplaceWorkflowRequest(
                    source_path="workflows/new.py",
                    function_name="run",
                ),
                ctx=None,
                user=SimpleNamespace(email="admin@example.com"),
                db=object(),
            )

    assert exc.value.status_code == 400
    assert exc.value.detail == "signature mismatch"


@pytest.mark.asyncio
async def test_deactivate_workflow_includes_plural_reference_warning():
    workflow_id = uuid4()
    service = SimpleNamespace(
        deactivate_workflow=AsyncMock(
            return_value=(SimpleNamespace(id=workflow_id), 2)
        )
    )

    with (
        patch.object(
            workflows,
            "assert_entity_id_not_solution_managed",
            new=AsyncMock(),
        ),
        patch(
            "src.services.workflow_orphan.WorkflowOrphanService",
            return_value=service,
        ),
    ):
        result = await workflows.deactivate_workflow(
            workflow_id,
            ctx=None,
            user=SimpleNamespace(email="admin@example.com"),
            db=object(),
        )

    assert result.success is True
    assert result.workflow_id == str(workflow_id)
    assert result.warning == "2 forms/apps still reference this workflow"


@pytest.mark.asyncio
async def test_deactivate_workflow_omits_warning_without_references():
    workflow_id = uuid4()
    service = SimpleNamespace(
        deactivate_workflow=AsyncMock(
            return_value=(SimpleNamespace(id=workflow_id), 0)
        )
    )

    with (
        patch.object(
            workflows,
            "assert_entity_id_not_solution_managed",
            new=AsyncMock(),
        ),
        patch(
            "src.services.workflow_orphan.WorkflowOrphanService",
            return_value=service,
        ),
    ):
        result = await workflows.deactivate_workflow(
            workflow_id,
            ctx=None,
            user=SimpleNamespace(email="admin@example.com"),
            db=object(),
        )

    assert result.warning is None

