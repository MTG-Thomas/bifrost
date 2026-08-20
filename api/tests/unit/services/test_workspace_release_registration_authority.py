"""Global Live owns governed workflow registration mutations."""

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock
from uuid import uuid4

import pytest

from src.services import workspace_release_registration_authority as authority


def _release():
    workflow_id = uuid4()
    return SimpleNamespace(
        release_id="sha256:" + "a" * 64,
        governed_paths=("features/live.py",),
        effective_registrations={
            "features/live.py::run": {
                "workflow_id": str(workflow_id),
            }
        },
    ), workflow_id


@pytest.mark.asyncio
async def test_external_guard_serializes_and_blocks_governed_path(monkeypatch):
    release, _ = _release()
    acquire = AsyncMock()
    monkeypatch.setattr(authority, "acquire_workspace_release_lock", acquire)
    monkeypatch.setattr(
        authority,
        "global_active_workspace_release_descriptor",
        AsyncMock(return_value=release),
    )

    with pytest.raises(authority.WorkspaceReleaseRegistrationGoverned) as exc:
        await authority.guard_workspace_registration_mutation(
            SimpleNamespace(),
            operation="register workflow",
            paths=("/features\\live.py",),
        )

    acquire.assert_awaited_once_with(ANY, None)
    assert exc.value.reference == "features/live.py"
    assert exc.value.release_id == release.release_id


@pytest.mark.asyncio
@pytest.mark.parametrize("match", ["id", "key"])
async def test_external_guard_blocks_effective_registration_after_row_drift(
    monkeypatch, match
):
    release, workflow_id = _release()
    monkeypatch.setattr(authority, "acquire_workspace_release_lock", AsyncMock())
    monkeypatch.setattr(
        authority,
        "global_active_workspace_release_descriptor",
        AsyncMock(return_value=release),
    )
    workflow = SimpleNamespace(
        id=workflow_id if match == "id" else uuid4(),
        path="features/renamed.py" if match == "id" else "features/live.py",
        function_name="renamed" if match == "id" else "run",
    )

    with pytest.raises(authority.WorkspaceReleaseRegistrationGoverned):
        await authority.guard_workspace_registration_mutation(
            SimpleNamespace(),
            operation="update workflow",
            workflows=(workflow,),
        )


@pytest.mark.asyncio
async def test_external_guard_allows_ungoverned_registration(monkeypatch):
    release, _ = _release()
    monkeypatch.setattr(authority, "acquire_workspace_release_lock", AsyncMock())
    monkeypatch.setattr(
        authority,
        "global_active_workspace_release_descriptor",
        AsyncMock(return_value=release),
    )

    await authority.guard_workspace_registration_mutation(
        SimpleNamespace(),
        operation="register workflow",
        paths=("features/legacy.py",),
        workflows=(
            SimpleNamespace(
                id=uuid4(),
                path="features/legacy.py",
                function_name="run",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_release_activation_authority_is_explicit_internal_bypass(monkeypatch):
    acquire = AsyncMock()
    load_release = AsyncMock()
    monkeypatch.setattr(authority, "acquire_workspace_release_lock", acquire)
    monkeypatch.setattr(
        authority, "global_active_workspace_release_descriptor", load_release
    )

    await authority.guard_workspace_registration_mutation(
        SimpleNamespace(),
        operation="activate reviewed release",
        paths=("features/live.py",),
        authority=authority.WorkspaceRegistrationMutationAuthority.RELEASE_ACTIVATION,
    )

    acquire.assert_not_awaited()
    load_release.assert_not_awaited()
