"""Registration and execution contract for durable Workspace previews."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from src.jobs.platform.base import PlatformJobFailure
from src.jobs.platform.registry import get_platform_job_definition
from src.jobs.platform.workspace_promotion_preview import (
    WORKSPACE_PROMOTION_PREVIEW_DEFINITION,
    WorkspacePromotionPreviewPayload,
    run_workspace_promotion_preview,
)
from src.models.contracts.workspace_promotions import WorkspacePromotionPreviewRequest
from src.services.workspace_promotions import WorkspacePromotionInvalid


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


def test_workspace_promotion_preview_is_a_bounded_durable_job() -> None:
    definition = get_platform_job_definition("workspace.promotion.preview")

    assert definition is WORKSPACE_PROMOTION_PREVIEW_DEFINITION
    assert definition.payload_version == 1
    assert definition.policy.max_attempts == 2
    assert definition.policy.max_concurrency == 2
    assert definition.policy.timeout_seconds == 15 * 60


@pytest.mark.asyncio
async def test_preview_job_returns_serializable_preview(monkeypatch) -> None:
    organization_id = uuid4()
    expected = {"candidate_id": "sha256:" + "5" * 64, "diagnostics": []}

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace()

    service = SimpleNamespace(
        preview=AsyncMock(
            return_value=SimpleNamespace(
                candidate_id=expected["candidate_id"],
                model_dump=lambda **_kwargs: expected,
            )
        )
    )
    factory = AsyncMock(return_value=service)
    monkeypatch.setattr(
        "src.jobs.platform.workspace_promotion_preview.get_db_context", db_context
    )
    monkeypatch.setattr(
        "src.jobs.platform.workspace_promotion_preview.build_workspace_promotion_preview_service",
        factory,
    )
    user_id = uuid4()
    context = SimpleNamespace(
        organization_id=organization_id,
        requested_by_user_id=str(user_id),
        log=AsyncMock(),
    )

    result = await run_workspace_promotion_preview(
        context, WorkspacePromotionPreviewPayload(request=_request())
    )

    assert result == expected
    factory.assert_awaited_once()
    service.preview.assert_awaited_once_with(_request(), user_id)
    context.log.assert_awaited_once()


@pytest.mark.asyncio
async def test_preview_job_exposes_validation_failure(monkeypatch) -> None:
    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace()

    service = SimpleNamespace(
        preview=AsyncMock(side_effect=WorkspacePromotionInvalid("bad preview"))
    )
    monkeypatch.setattr(
        "src.jobs.platform.workspace_promotion_preview.get_db_context", db_context
    )
    monkeypatch.setattr(
        "src.jobs.platform.workspace_promotion_preview.build_workspace_promotion_preview_service",
        AsyncMock(return_value=service),
    )
    context = SimpleNamespace(
        organization_id=uuid4(),
        requested_by_user_id=str(uuid4()),
        log=AsyncMock(),
    )

    with pytest.raises(PlatformJobFailure, match="bad preview") as exc_info:
        await run_workspace_promotion_preview(
            context, WorkspacePromotionPreviewPayload(request=_request())
        )

    assert exc_info.value.code == "workspace_promotion_preview_invalid"
    assert exc_info.value.retryable is False
