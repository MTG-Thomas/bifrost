"""Registration and policy contract for Workspace release preparation."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bifrost.workspace_release_authorization import computed_effects_id

from src.jobs.platform.registry import get_platform_job_definition
from src.jobs.platform.workspace_release_prepare import (
    WORKSPACE_RELEASE_PREPARE_DEFINITION,
    WorkspaceReleasePreparePayload,
    run_workspace_release_prepare,
)


def test_workspace_release_prepare_is_a_bounded_durable_job() -> None:
    definition = get_platform_job_definition("workspace.release.prepare")

    assert definition is WORKSPACE_RELEASE_PREPARE_DEFINITION
    assert definition.payload_version == 1
    assert definition.policy.max_attempts == 2
    assert definition.policy.max_concurrency == 2
    assert definition.policy.timeout_seconds == 15 * 60


@pytest.mark.asyncio
async def test_prepare_job_returns_exact_risk_authorization_challenge(
    monkeypatch,
) -> None:
    organization_id = uuid4()
    artifact_id = uuid4()
    release_id = "sha256:" + "2" * 64
    evidence = {
        "artifact_id": str(artifact_id),
        "candidate_id": "sha256:" + "1" * 64,
        "release_id": release_id,
        "evidence_id": "sha256:" + "3" * 64,
        "effective_manifest_id": "sha256:" + "4" * 64,
        "governed_manifest_id": "sha256:" + "5" * 64,
        "effective_registration_manifest_id": "sha256:" + "6" * 64,
        "risk_class": "R2",
        "computed_effects": ["integration.write:halopsa"],
        "computed_effects_id": computed_effects_id(["integration.write:halopsa"]),
        "policy_version": "test",
        "protected_source": {"commit_sha": "8" * 40, "tree_sha": "9" * 40},
        "effect_execution": "not_performed",
        "runtime_storage_prefix": "immutable/",
        "file_count": 2,
        "total_bytes": 100,
    }

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace()

    class Materializer:
        def __init__(self, _db, _organization_id):
            assert _organization_id == organization_id

        async def prepare(self, *_args, **_kwargs):
            return SimpleNamespace(id=uuid4()), evidence

    monkeypatch.setattr(
        "src.jobs.platform.workspace_release_prepare.get_db_context", db_context
    )
    monkeypatch.setattr(
        "src.jobs.platform.workspace_release_prepare.WorkspaceReleaseMaterializer",
        Materializer,
    )
    context = SimpleNamespace(
        organization_id=organization_id,
        requested_by_user_id=str(uuid4()),
        report=AsyncMock(),
        log=AsyncMock(),
    )

    result = await run_workspace_release_prepare(
        context,
        WorkspaceReleasePreparePayload(
            artifact_id=artifact_id,
            candidate_id=evidence["candidate_id"],
        ),
    )

    assert result["risk_class"] == "R2"
    assert result["effect_execution"] == "not_performed"
    challenge = result["activation_authorization"]
    assert challenge["governed_manifest_id"] == evidence["governed_manifest_id"]
    assert challenge["required_authorization"] == "risk_acknowledgement"
