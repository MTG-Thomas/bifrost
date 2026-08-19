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
async def test_activation_makes_live_durable_before_projection_is_queued(
    monkeypatch,
) -> None:
    events: list[str] = []
    result = SimpleNamespace(activation_state="live", lock_state="queued")
    job = SimpleNamespace(id=uuid4(), notification_id=uuid4())

    class Service:
        def __init__(self, _db, _organization_id):
            pass

        async def activate(self, _release_id, _request):
            events.append("live_committed")

        async def enqueue_projection(self, _release_id, **_operator):
            assert events == ["live_committed"]
            events.append("projection_queued")
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
    assert events == ["live_committed", "projection_queued"]
    publish.assert_awaited_once_with(job)


@pytest.mark.asyncio
async def test_projection_enqueue_failure_preserves_live_with_attention_evidence(
    monkeypatch,
) -> None:
    events: list[str] = []
    release_id = uuid4()
    db = SimpleNamespace(rollback=AsyncMock())
    attention = SimpleNamespace(
        activation_state="live",
        lock_state="attention_required",
        error_code="workspace_release_lock_queue_failed",
    )

    class Service:
        def __init__(self, _db, _organization_id):
            pass

        async def activate(self, _release_id, _request):
            events.append("live_committed")

        async def enqueue_projection(self, _release_id, **_operator):
            assert events == ["live_committed"]
            raise RuntimeError("queue unavailable")

        async def mark_projection_queue_failed(self, target_id, message):
            assert target_id == release_id
            assert message == "queue unavailable"
            events.append("attention_recorded")
            return attention

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

    assert response is attention
    assert response.activation_state == "live"
    assert response.lock_state == "attention_required"
    assert response.error_code == "workspace_release_lock_queue_failed"
    assert events == ["live_committed", "attention_recorded"]
    db.rollback.assert_awaited_once()
