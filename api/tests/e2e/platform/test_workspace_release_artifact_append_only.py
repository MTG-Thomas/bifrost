"""Database enforcement for immutable Workspace release artifacts."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError

from src.models.orm.workspace_promotions import (
    WorkspacePromotionArtifact,
    WorkspacePromotionRelease,
)
from src.models.orm.organizations import Organization


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


@pytest.mark.e2e
async def test_expired_draft_can_be_deleted_but_unexpired_draft_cannot(
    db_session, platform_admin, org1
) -> None:
    def draft(candidate: str, expires_at: datetime) -> WorkspacePromotionArtifact:
        return WorkspacePromotionArtifact(
            organization_id=org1["id"],
            candidate_id="sha256:" + candidate * 64,
            content_id="sha256:" + "2" * 64,
            closure_id="sha256:" + "3" * 64,
            schema_version="bifrost.workspace-draft-upload/v1",
            target_kind="draft",
            entity_type="workflow",
            entry_path=f"workflows/draft_{candidate}.py",
            entry_function="run",
            snapshot_id="sha256:" + "4" * 64,
            source_artifact_key=f"test/{candidate}/source.zip",
            manifest_key=f"test/{candidate}/manifest.json",
            manifest={},
            risk_class="R0",
            disposition="review_required",
            artifact_state="previewed",
            policy_version="test",
            created_by=platform_admin.user_id,
            expires_at=expires_at,
        )

    expired = draft("5", datetime.now(timezone.utc) - timedelta(minutes=1))
    current = draft("6", datetime.now(timezone.utc) + timedelta(hours=1))
    db_session.add_all([expired, current])
    await db_session.commit()

    await db_session.execute(
        delete(WorkspacePromotionArtifact).where(
            WorkspacePromotionArtifact.id == expired.id
        )
    )
    await db_session.commit()

    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(
            delete(WorkspacePromotionArtifact).where(
                WorkspacePromotionArtifact.id == current.id
            )
        )
    await db_session.rollback()


@pytest.mark.e2e
async def test_organization_delete_can_cascade_reviewed_artifacts(
    db_session, platform_admin
) -> None:
    organization = Organization(name="Artifact Cascade", created_by="test")
    db_session.add(organization)
    await db_session.flush()
    artifact = WorkspacePromotionArtifact(
        organization_id=organization.id,
        candidate_id="sha256:" + "7" * 64,
        content_id="sha256:" + "8" * 64,
        closure_id="sha256:" + "9" * 64,
        release_id="sha256:" + "a" * 64,
        base_release_id="repo-v1:" + "b" * 64,
        base_manifest_id="sha256:" + "c" * 64,
        effective_manifest_id="sha256:" + "d" * 64,
        effective_registration_manifest_id="sha256:" + "e" * 64,
        registration_intent_fingerprint="sha256:" + "f" * 64,
        registration_state_fingerprint="sha256:" + "0" * 64,
        schema_version="bifrost.workspace-promotion-bundle/v2",
        target_kind="workspace",
        entity_type="workflow",
        entry_path="workflows/cascade.py",
        entry_function="run",
        snapshot_id="sha256:" + "1" * 64,
        source_revision="2" * 40,
        source_tree_sha="3" * 40,
        source_artifact_key="test/cascade/source.zip",
        manifest_key="test/cascade/manifest.json",
        manifest={},
        risk_class="R0",
        disposition="review_required",
        artifact_state="review_required",
        policy_version="test",
        created_by=platform_admin.user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(artifact)
    await db_session.flush()
    successor = WorkspacePromotionArtifact(
        organization_id=organization.id,
        candidate_id="sha256:" + "4" * 64,
        content_id="sha256:" + "5" * 64,
        closure_id="sha256:" + "6" * 64,
        release_id="sha256:" + "7" * 64,
        base_release_id=artifact.release_id,
        base_manifest_id="sha256:" + "8" * 64,
        effective_manifest_id="sha256:" + "9" * 64,
        effective_registration_manifest_id="sha256:" + "a" * 64,
        registration_intent_fingerprint="sha256:" + "b" * 64,
        registration_state_fingerprint="sha256:" + "c" * 64,
        schema_version="bifrost.workspace-promotion-bundle/v2",
        target_kind="workspace",
        entity_type="workflow",
        entry_path="workflows/cascade_successor.py",
        entry_function="run",
        snapshot_id="sha256:" + "d" * 64,
        source_revision="e" * 40,
        source_tree_sha="f" * 40,
        source_artifact_key="test/cascade-successor/source.zip",
        manifest_key="test/cascade-successor/manifest.json",
        manifest={},
        risk_class="R0",
        disposition="review_required",
        artifact_state="review_required",
        policy_version="test",
        created_by=platform_admin.user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        supersedes_artifact_id=artifact.id,
    )
    release = WorkspacePromotionRelease(
        organization_id=organization.id,
        artifact_id=artifact.id,
        activation_state="prepared",
        lock_state="not_queued",
        created_by=platform_admin.user_id,
    )
    db_session.add_all([successor, release])
    await db_session.commit()
    artifact_id = artifact.id
    successor_id = successor.id
    release_id = release.id

    await db_session.delete(organization)
    await db_session.commit()

    assert await db_session.get(WorkspacePromotionArtifact, artifact_id) is None
    assert await db_session.get(WorkspacePromotionArtifact, successor_id) is None
    assert await db_session.get(WorkspacePromotionRelease, release_id) is None


@pytest.mark.e2e
async def test_release_cannot_reference_another_organizations_artifact(
    db_session, platform_admin
) -> None:
    artifact_org = Organization(name="Artifact Owner", created_by="test")
    release_org = Organization(name="Release Owner", created_by="test")
    db_session.add_all([artifact_org, release_org])
    await db_session.flush()
    artifact = WorkspacePromotionArtifact(
        organization_id=artifact_org.id,
        candidate_id="sha256:" + "1" * 64,
        schema_version="bifrost.workspace-promotion-bundle/v2",
        target_kind="workspace",
        entity_type="workflow",
        entry_path="workflows/cross_org.py",
        entry_function="run",
        snapshot_id="sha256:" + "2" * 64,
        source_artifact_key="test/cross-org/source.zip",
        manifest_key="test/cross-org/manifest.json",
        manifest={},
        risk_class="R0",
        disposition="review_required",
        artifact_state="review_required",
        policy_version="test",
        created_by=platform_admin.user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(artifact)
    await db_session.flush()
    db_session.add(
        WorkspacePromotionRelease(
            organization_id=release_org.id,
            artifact_id=artifact.id,
            activation_state="prepared",
            lock_state="not_queued",
            created_by=platform_admin.user_id,
        )
    )

    with pytest.raises(DBAPIError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.e2e
async def test_live_release_cannot_commit_without_projection_job(
    db_session, platform_admin
) -> None:
    organization = Organization(name="Live Job Invariant", created_by="test")
    db_session.add(organization)
    await db_session.flush()
    artifact = WorkspacePromotionArtifact(
        organization_id=organization.id,
        candidate_id="sha256:" + "3" * 64,
        schema_version="bifrost.workspace-promotion-bundle/v2",
        target_kind="workspace",
        entity_type="workflow",
        entry_path="workflows/live_job.py",
        entry_function="run",
        snapshot_id="sha256:" + "4" * 64,
        source_artifact_key="test/live-job/source.zip",
        manifest_key="test/live-job/manifest.json",
        manifest={},
        risk_class="R0",
        disposition="review_required",
        artifact_state="review_required",
        policy_version="test",
        created_by=platform_admin.user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(artifact)
    await db_session.flush()
    db_session.add(
        WorkspacePromotionRelease(
            organization_id=organization.id,
            artifact_id=artifact.id,
            activation_state="live",
            lock_state="queued",
            lock_in_job_id=None,
            created_by=platform_admin.user_id,
        )
    )

    with pytest.raises(DBAPIError):
        await db_session.commit()
    await db_session.rollback()
