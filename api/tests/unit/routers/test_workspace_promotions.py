"""Route-level contracts for immutable Workspace release activation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from src.models.contracts.workspace_promotions import (
    WorkspaceSourceReleaseDeclareRequest,
)
from src.routers import workspace_promotions
from src.services.github_actions_oidc import (
    GitHubActionsOIDCError,
    WorkspaceSourceReleaseProducer,
)


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


def _source_declaration(commit_sha: str) -> WorkspaceSourceReleaseDeclareRequest:
    return WorkspaceSourceReleaseDeclareRequest(
        source_commit_sha=commit_sha,
        source_tree_sha="b" * 40,
        paths={"workflows/example.py": "c" * 64},
        disposition="pending",
    )


@pytest.mark.asyncio
async def test_github_source_release_dependency_rejects_missing_bearer() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await workspace_promotions._github_source_release_producer(None)

    assert exc_info.value.status_code == 401
    assert "bearer token is required" in exc_info.value.detail


@pytest.mark.asyncio
async def test_github_source_release_dependency_rejects_partial_configuration(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        workspace_promotions,
        "get_settings",
        lambda: SimpleNamespace(
            workspace_source_release_oidc_repository=("MTG-Thomas/bifrost-workspace")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace_promotions._github_source_release_producer(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
        )

    assert exc_info.value.status_code == 503
    assert "not configured" in exc_info.value.detail


@pytest.mark.asyncio
async def test_github_source_release_dependency_rejects_invalid_oidc_token(
    monkeypatch,
) -> None:
    settings = SimpleNamespace(
        workspace_source_release_oidc_repository="MTG-Thomas/bifrost-workspace",
        workspace_source_release_oidc_repository_id=1197464564,
        workspace_source_release_oidc_repository_owner_id=87775189,
        workspace_source_release_oidc_workflow_ref=(
            "MTG-Thomas/bifrost-workspace/.github/workflows/"
            "declare-workspace-source-release.yml@refs/heads/main"
        ),
        workspace_source_release_oidc_organization_id=str(uuid4()),
    )
    monkeypatch.setattr(workspace_promotions, "get_settings", lambda: settings)
    authenticate = AsyncMock(
        side_effect=GitHubActionsOIDCError("token rejected by pinned policy")
    )
    monkeypatch.setattr(
        workspace_promotions,
        "authenticate_workspace_source_release_producer",
        authenticate,
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace_promotions._github_source_release_producer(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "token rejected by pinned policy"
    authenticate.assert_awaited_once_with("token", settings=settings)


@pytest.mark.asyncio
async def test_github_declaration_requires_body_sha_to_match_oidc_sha(
    monkeypatch,
) -> None:
    service = MagicMock(side_effect=AssertionError("mismatch must not be recorded"))
    monkeypatch.setattr(workspace_promotions, "WorkspaceSourceReleaseService", service)
    producer = WorkspaceSourceReleaseProducer(
        organization_id=uuid4(),
        source_commit_sha="a" * 40,
        repository="MTG-Thomas/bifrost-workspace",
        workflow_ref="trusted",
        run_id="123",
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace_promotions.declare_workspace_source_release_from_github(
            _source_declaration("d" * 40),
            SimpleNamespace(),
            producer,
        )

    assert exc_info.value.status_code == 403
    service.assert_not_called()


@pytest.mark.asyncio
async def test_github_declaration_uses_pinned_organization_and_system_actor(
    monkeypatch,
) -> None:
    organization_id = uuid4()
    response = SimpleNamespace(id=uuid4())
    captured: dict[str, object] = {}

    class Service:
        def __init__(self, db, org_id):
            captured["db"] = db
            captured["organization_id"] = org_id

        async def declare(self, request, *, created_by):
            captured["request"] = request
            captured["created_by"] = created_by
            return response

    monkeypatch.setattr(workspace_promotions, "WorkspaceSourceReleaseService", Service)
    producer = WorkspaceSourceReleaseProducer(
        organization_id=organization_id,
        source_commit_sha="a" * 40,
        repository="MTG-Thomas/bifrost-workspace",
        workflow_ref="trusted",
        run_id="123",
    )
    request = _source_declaration("a" * 40)
    db = SimpleNamespace()

    result = await workspace_promotions.declare_workspace_source_release_from_github(
        request,
        db,
        producer,
    )

    assert result is response
    assert captured["organization_id"] == organization_id
    assert captured["request"] is request
    assert captured["created_by"] == workspace_promotions.SYSTEM_USER_UUID
