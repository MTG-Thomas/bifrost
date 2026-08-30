from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models import UpdatePropertiesRequest
from src.routers import decorator_properties
from src.services.workspace_release_files import WorkspaceReleasePathGoverned


@pytest.mark.asyncio
async def test_get_properties_reads_governed_immutable_live(monkeypatch):
    path = "workflows/governed.py"
    view = MagicMock()
    view.governs.return_value = True
    view.read = AsyncMock(
        return_value=b'@workflow(name="Reviewed")\nasync def governed():\n    pass\n'
    )
    monkeypatch.setattr(
        decorator_properties,
        "active_workspace_release_file_view",
        AsyncMock(return_value=view),
    )
    storage = MagicMock()
    storage.read_file = AsyncMock(return_value=(b"@workflow\nasync def stale(): pass", None))
    monkeypatch.setattr(decorator_properties, "FileStorageService", lambda _db: storage)

    response = await decorator_properties.get_decorator_properties(
        ctx=SimpleNamespace(org_id=uuid4()),
        user=object(),
        path=path,
        db=object(),
    )

    assert response.decorators[0].function_name == "governed"
    view.read.assert_awaited_once_with(path)
    storage.read_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_properties_rejects_governed_path_before_read_or_write(monkeypatch):
    path = "workflows/governed.py"
    guard = AsyncMock(side_effect=WorkspaceReleasePathGoverned(path, "release-1"))
    storage = MagicMock()
    storage.read_file = AsyncMock()
    storage.write_file = AsyncMock()
    monkeypatch.setattr(
        decorator_properties,
        "reject_release_governed_paths",
        guard,
    )
    monkeypatch.setattr(decorator_properties, "FileStorageService", lambda _db: storage)

    with pytest.raises(HTTPException) as exc:
        await decorator_properties.update_decorator_properties(
            ctx=SimpleNamespace(org_id=uuid4()),
            user=SimpleNamespace(email="admin@example.com"),
            request=UpdatePropertiesRequest(
                path=path,
                function_name="governed",
                properties={"name": "Changed"},
            ),
            db=object(),
        )

    assert exc.value.status_code == 409
    storage.read_file.assert_not_awaited()
    storage.write_file.assert_not_awaited()
