"""Route-level contracts for immutable Workspace release activation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.routers import workspace_promotions


def _ctx():
    return SimpleNamespace(org_id=uuid4())


def _user():
    return SimpleNamespace(
        user_id=uuid4(),
        email="operator@example.test",
        name="Operator",
    )


@pytest.mark.asyncio
async def test_activation_uses_dedicated_fail_closed_feature_gate(monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_promotions,
        "get_settings",
        lambda: SimpleNamespace(
            workspace_rapid_promotion_preview_enabled=True,
            workspace_release_prepare_canary_enabled=True,
            workspace_release_activation_enabled=False,
        ),
    )
    service = MagicMock(side_effect=AssertionError("disabled route must not activate"))
    monkeypatch.setattr(
        workspace_promotions,
        "WorkspaceReleaseActivationService",
        service,
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace_promotions.activate_workspace_release(
            uuid4(),
            SimpleNamespace(),
            _ctx(),
            SimpleNamespace(),
            _user(),
        )

    assert exc_info.value.status_code == 404
    assert "activation is not enabled" in exc_info.value.detail
    service.assert_not_called()


@pytest.mark.asyncio
async def test_activation_commits_live_with_durable_projection_before_publish(
    monkeypatch,
) -> None:
    events: list[str] = []
    result = SimpleNamespace(activation_state="live", lock_state="queued")
    job = SimpleNamespace(id=uuid4(), notification_id=uuid4())

    class Service:
        def __init__(self, _db, _organization_id):
            pass

        async def activate(self, _release_id, _request, **_operator):
            events.append("live_and_projection_committed")
            return SimpleNamespace(activation_state="live")

        async def enqueue_projection(self, _release_id, **_operator):
            assert events == ["live_and_projection_committed"]
            events.append("projection_job_reused")
            return result, job, False

    monkeypatch.setattr(
        workspace_promotions,
        "get_settings",
        lambda: SimpleNamespace(workspace_release_activation_enabled=True),
    )
    monkeypatch.setattr(
        workspace_promotions, "WorkspaceReleaseActivationService", Service
    )
    publish = AsyncMock()
    monkeypatch.setattr(workspace_promotions, "publish_platform_job_update", publish)

    response = await workspace_promotions.activate_workspace_release(
        uuid4(),
        SimpleNamespace(),
        _ctx(),
        SimpleNamespace(),
        _user(),
    )

    assert response is result
    assert events == ["live_and_projection_committed", "projection_job_reused"]
    publish.assert_awaited_once_with(job)


@pytest.mark.asyncio
async def test_projection_publish_lookup_failure_preserves_durable_queued_live(
    monkeypatch,
) -> None:
    events: list[str] = []
    release_id = uuid4()
    db = SimpleNamespace(rollback=AsyncMock())
    live = SimpleNamespace(
        activation_state="live",
        lock_state="queued",
        error_code=None,
    )

    class Service:
        def __init__(self, _db, _organization_id):
            pass

        async def activate(self, _release_id, _request, **_operator):
            events.append("live_and_projection_committed")
            return live

        async def enqueue_projection(self, _release_id, **_operator):
            assert events == ["live_and_projection_committed"]
            raise RuntimeError("queue unavailable")

    monkeypatch.setattr(
        workspace_promotions,
        "get_settings",
        lambda: SimpleNamespace(workspace_release_activation_enabled=True),
    )
    monkeypatch.setattr(
        workspace_promotions, "WorkspaceReleaseActivationService", Service
    )

    response = await workspace_promotions.activate_workspace_release(
        release_id,
        SimpleNamespace(),
        _ctx(),
        db,
        _user(),
    )

    assert response is live
    assert response.activation_state == "live"
    assert response.lock_state == "queued"
    assert response.error_code is None
    assert events == ["live_and_projection_committed"]
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_unexpected_activation_failure_is_not_masked_as_projection_failure(
    monkeypatch,
) -> None:
    class Service:
        def __init__(self, _db, _organization_id):
            pass

        async def activate(self, _release_id, _request, **_operator):
            raise RuntimeError("activation transaction failed")

        async def enqueue_projection(self, *_args, **_kwargs):
            raise AssertionError("projection must not run")

        async def mark_projection_queue_failed(self, *_args, **_kwargs):
            raise AssertionError("prepared release must not be marked Live")

    monkeypatch.setattr(
        workspace_promotions,
        "get_settings",
        lambda: SimpleNamespace(workspace_release_activation_enabled=True),
    )
    monkeypatch.setattr(
        workspace_promotions, "WorkspaceReleaseActivationService", Service
    )

    with pytest.raises(RuntimeError, match="activation transaction failed"):
        await workspace_promotions.activate_workspace_release(
            uuid4(),
            SimpleNamespace(),
            _ctx(),
            SimpleNamespace(),
            _user(),
        )
