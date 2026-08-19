"""Database enforcement for immutable Workspace release artifacts."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError

from src.models.orm.workspace_promotions import WorkspacePromotionArtifact


@pytest.mark.e2e
async def test_workspace_release_artifact_rejects_update_and_delete(
    db_session, platform_admin, org1
) -> None:
    artifact = WorkspacePromotionArtifact(
        organization_id=org1["id"],
        candidate_id="sha256:" + "1" * 64,
        content_id="sha256:" + "2" * 64,
        closure_id="sha256:" + "3" * 64,
        release_id="sha256:" + "4" * 64,
        base_release_id="repo-v1:" + "5" * 64,
        base_manifest_id="sha256:" + "6" * 64,
        effective_manifest_id="sha256:" + "7" * 64,
        effective_registration_manifest_id="sha256:" + "8" * 64,
        registration_intent_fingerprint="sha256:" + "9" * 64,
        registration_state_fingerprint="sha256:" + "a" * 64,
        schema_version="bifrost.workspace-promotion-bundle/v2",
        target_kind="workspace",
        entity_type="workflow",
        entry_path="workflows/append_only.py",
        entry_function="append_only",
        snapshot_id="sha256:" + "b" * 64,
        source_revision="c" * 40,
        source_tree_sha="d" * 40,
        source_artifact_key="test/source.zip",
        manifest_key="test/manifest.json",
        manifest={},
        risk_class="R0",
        disposition="review_required",
        artifact_state="review_required",
        policy_version="test",
        created_by=platform_admin.user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db_session.add(artifact)
    await db_session.commit()

    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(
            update(WorkspacePromotionArtifact)
            .where(WorkspacePromotionArtifact.id == artifact.id)
            .values(artifact_state="invalid")
        )
    await db_session.rollback()

    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(
            delete(WorkspacePromotionArtifact).where(
                WorkspacePromotionArtifact.id == artifact.id
            )
        )
    await db_session.rollback()
