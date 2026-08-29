"""Route contract for additive asynchronous Workspace previews."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Response
from src.models.contracts.platform_jobs import PlatformJobStatus
from src.models.contracts.workspace_promotions import WorkspacePromotionPreviewRequest
from src.routers import workspace_promotions


def _request() -> WorkspacePromotionPreviewRequest:
    return WorkspacePromotionPreviewRequest.model_validate(
        {
            "schema_version": "bifrost.workspace-promotion-bundle/v2",
            "entry": {"path": "workflows/example.py", "function": "example"},
            "snapshot": {
                "snapshot_id": "sha256:" + "1" * 64,
                "files": {"workflows/example.py": "2" * 64},
                "closure": [{"path": "workflows/example.py", "sha256": "2" * 64}],
            },
            "protected_source": {"commit_sha": "3" * 40, "tree_sha": "4" * 40},
            "client": {
                "cli_version": "1",
                "sdk_version": "1",
                "contract_version": "11",
            },
        }
    )


@pytest.mark.asyncio
async def test_preview_job_route_returns_durable_location(monkeypatch) -> None:
    job_id = uuid4()
    job = SimpleNamespace(
        id=job_id,
        notification_id=uuid4(),
        status="queued",
        requested_by_user_id=str(uuid4()),
    )
    enqueue = AsyncMock(return_value=(job, False))
    publish = AsyncMock()
    monkeypatch.setattr(
        workspace_promotions,
        "get_settings",
        lambda: SimpleNamespace(workspace_rapid_promotion_preview_enabled=True),
    )
    monkeypatch.setattr(workspace_promotions, "enqueue_platform_job", enqueue)
    monkeypatch.setattr(workspace_promotions, "publish_platform_job_update", publish)
    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
    user = SimpleNamespace(
        user_id=uuid4(), email="operator@example.test", name="Operator"
    )
    response = Response()

    accepted = await workspace_promotions.enqueue_workspace_promotion_preview(
        _request(), response, SimpleNamespace(org_id=uuid4()), db, user
    )

    assert accepted.job_id == job_id
    assert accepted.status is PlatformJobStatus.QUEUED
    assert response.headers["Location"] == f"/api/platform-jobs/{job_id}"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(job)
    publish.assert_awaited_once_with(job)
    kwargs = enqueue.await_args.kwargs
    assert kwargs["dedupe_key"]
    assert kwargs["resource_lock_key"].startswith("workspace-promotion-preview:")
