from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from bifrost.workspace_release import (
    workspace_manifest_id,
    workspace_registration_manifest_id,
)
from src.models.orm.workspace_promotions import (
    WorkspacePromotionArtifact,
    WorkspacePromotionRelease,
)
from src.services.workspace_release_runtime import (
    WorkspaceReleaseDescriptor,
    WorkspaceReleaseRuntimeError,
    inspect_workspace_release_coherence,
    PinnedWorkspaceRuntime,
    resolve_pinned_workspace_runtime,
    verify_workspace_runtime_evidence,
    workflow_data_from_workspace_evidence,
)


def _rows() -> tuple[WorkspacePromotionRelease, WorkspacePromotionArtifact]:
    organization_id = uuid4()
    artifact_id = uuid4()
    release_id = "sha256:" + "a" * 64
    files = {
        "features/demo.py": "b" * 64,
        "modules/helper.py": "c" * 64,
    }
    registrations = {
        "features/demo.py::run": {
            "path": "features/demo.py",
            "function": "run",
            "workflow_id": str(uuid4()),
            "type": "workflow",
            "name": "Demo",
            "organization_id": str(organization_id),
            "is_active": True,
            "source_sha256": "b" * 64,
        }
    }
    now = datetime.now(timezone.utc)
    artifact = WorkspacePromotionArtifact(
        id=artifact_id,
        organization_id=organization_id,
        candidate_id="sha256:" + "d" * 64,
        schema_version="bifrost.workspace-release-artifact/v1",
        target_kind="workspace",
        entity_type="workflow",
        entry_path="features/demo.py",
        entry_function="run",
        snapshot_id="sha256:" + "e" * 64,
        source_revision="1" * 40,
        source_artifact_key="_workspace_promotion_artifacts/source.zip",
        manifest_key="_workspace_promotion_artifacts/manifest.json",
        manifest={
            "schema_version": "bifrost.workspace-release-artifact/v1",
            "release_id": release_id,
            "effective_manifest_id": workspace_manifest_id(files),
            "effective_files": files,
            "effective_registration_manifest_id": workspace_registration_manifest_id(
                registrations
            ),
            "effective_registrations": registrations,
            "protected_source": {
                "commit_sha": "1" * 40,
                "tree_sha": "2" * 40,
            },
            "registration": {"state_fingerprint": "sha256:" + "f" * 64},
        },
        risk_class="R0",
        disposition="review_required",
        artifact_state="eligible",
        policy_version="test",
        created_by=uuid4(),
        expires_at=now + timedelta(hours=1),
        created_at=now,
    )
    release = WorkspacePromotionRelease(
        id=uuid4(),
        organization_id=organization_id,
        artifact_id=artifact_id,
        activation_state="live",
        lock_state="not_queued",
        created_by=uuid4(),
    )
    return release, artifact


def test_descriptor_binds_release_id_to_complete_effective_manifest() -> None:
    release, artifact = _rows()

    descriptor = WorkspaceReleaseDescriptor.from_rows(release, artifact)

    assert descriptor.release_id == "sha256:" + "a" * 64
    assert descriptor.source_hashes == artifact.manifest["effective_files"]
    assert descriptor.runtime_storage_prefix == (
        f"_workspace_releases/{release.organization_id}/{'a' * 64}/files/"
    )


def test_descriptor_rejects_manifest_digest_drift() -> None:
    release, artifact = _rows()
    artifact.manifest = {
        **artifact.manifest,
        "effective_files": {
            **artifact.manifest["effective_files"],
            "modules/helper.py": "0" * 64,
        },
    }

    with pytest.raises(WorkspaceReleaseRuntimeError, match="digest does not match"):
        WorkspaceReleaseDescriptor.from_rows(release, artifact)


def test_queue_pin_must_match_durable_and_authoritative_release() -> None:
    evidence = {
        "schema_version": "bifrost.workspace-release-runtime/v1",
        "workspace_release_id": "sha256:" + "a" * 64,
    }
    from src.services.workspace_release_runtime import _canonical_hash

    verify_workspace_runtime_evidence(
        evidence,
        dict(evidence),
        _canonical_hash(evidence),
        dict(evidence),
    )

    with pytest.raises(WorkspaceReleaseRuntimeError, match="immutable artifact"):
        verify_workspace_runtime_evidence(
            evidence,
            dict(evidence),
            _canonical_hash(evidence),
            {**evidence, "workspace_release_id": "sha256:" + "b" * 64},
        )


def test_entry_source_must_be_a_member_of_same_release() -> None:
    evidence = {
        "workspace_release_id": "sha256:" + "a" * 64,
        "workspace_release_runtime_storage_prefix": (
            "_workspace_releases/org/release/files/"
        ),
        "workspace_release_source_hashes": {"features/demo.py": "b" * 64},
        "workflow_name": "Demo",
        "workflow_function_name": "run",
        "workflow_path": "features/demo.py",
        "workflow_source_hash": "c" * 64,
    }

    with pytest.raises(WorkspaceReleaseRuntimeError, match="outside its effective"):
        workflow_data_from_workspace_evidence(evidence)


@pytest.mark.asyncio
async def test_superseded_release_remains_valid_for_durable_queued_pin() -> None:
    release_row, artifact = _rows()
    descriptor = WorkspaceReleaseDescriptor.from_rows(release_row, artifact)
    registration = next(iter(descriptor.effective_registrations.values()))
    workflow_id = UUID(registration["workflow_id"])
    pinned = PinnedWorkspaceRuntime(
        workflow_id=workflow_id,
        release=descriptor,
        name="Demo",
        function_name="run",
        path="features/demo.py",
        source_hash="b" * 64,
        timeout_seconds=30,
        time_saved=0,
        value=0,
        execution_mode="async",
        workflow_type="workflow",
        cache_ttl_seconds=0,
        organization_id=str(release_row.organization_id),
    )
    evidence = pinned.queue_evidence()
    release_row.activation_state = "superseded"

    class Result:
        def one_or_none(self):
            return release_row, artifact

    workflow = SimpleNamespace(
        id=workflow_id,
        is_active=True,
        solution_id=None,
        organization_id=release_row.organization_id,
    )

    class Session:
        async def execute(self, _statement):
            return Result()

        async def get(self, _model, _identity):
            return workflow

    resolved = await resolve_pinned_workspace_runtime(
        Session(), evidence, workflow_id
    )

    assert resolved.queue_evidence() == evidence


@pytest.mark.asyncio
async def test_inspector_exposes_current_immutable_tree_and_stale_cache_and_repo(
    monkeypatch,
) -> None:
    release_row, artifact = _rows()
    expected_content = b"VALUE = 'reviewed'\n"
    stale_content = b"VALUE = 'stale'\n"
    expected_hash = hashlib.sha256(expected_content).hexdigest()
    files = {"modules/helper.py": expected_hash}
    artifact.manifest = {
        **artifact.manifest,
        "effective_manifest_id": workspace_manifest_id(files),
        "effective_files": files,
        "effective_registration_manifest_id": workspace_registration_manifest_id({}),
        "effective_registrations": {},
    }
    descriptor = WorkspaceReleaseDescriptor.from_rows(release_row, artifact)

    class ReleaseStorage:
        def __init__(self, _prefix):
            pass

        async def read_many(self, _paths):
            return {"modules/helper.py": expected_content}

    class Repo:
        async def read(self, _path):
            return stale_content

    stale_cache = json.dumps(
        {
            "content": stale_content.decode(),
            "hash": hashlib.sha256(stale_content).hexdigest(),
            "path": "modules/helper.py",
        }
    )
    redis = SimpleNamespace(mget=lambda _keys: None)

    async def mget(_keys):
        return [stale_cache]

    redis.mget = mget
    monkeypatch.setattr(
        "src.services.workspace_release_storage.WorkspaceReleaseStorage",
        ReleaseStorage,
    )
    monkeypatch.setattr("src.services.repo_storage.RepoStorage", Repo)
    async def get_redis():
        return redis

    monkeypatch.setattr(
        "src.core.redis_client.get_redis_client",
        lambda: SimpleNamespace(_get_redis=get_redis),
    )

    coherent, evidence = await inspect_workspace_release_coherence(descriptor)

    assert coherent is False
    assert evidence[0].immutable_coherent is True
    assert evidence[0].cache_coherent is False
    assert evidence[0].projected_repo_coherent is False
    assert evidence[0].history_coherent is None


@pytest.mark.asyncio
async def test_inspector_fails_closed_when_immutable_release_bytes_regress(
    monkeypatch,
) -> None:
    release_row, artifact = _rows()
    expected_content = b"VALUE = 'reviewed'\n"
    stale_content = b"VALUE = 'rolled back'\n"
    expected_hash = hashlib.sha256(expected_content).hexdigest()
    files = {"modules/helper.py": expected_hash}
    artifact.manifest = {
        **artifact.manifest,
        "effective_manifest_id": workspace_manifest_id(files),
        "effective_files": files,
        "effective_registration_manifest_id": workspace_registration_manifest_id({}),
        "effective_registrations": {},
    }
    descriptor = WorkspaceReleaseDescriptor.from_rows(release_row, artifact)

    class ReleaseStorage:
        def __init__(self, _prefix):
            pass

        async def read_many(self, _paths):
            return {"modules/helper.py": stale_content}

    class Repo:
        async def read(self, _path):
            return expected_content

    cache = json.dumps(
        {
            "content": expected_content.decode(),
            "hash": expected_hash,
            "path": "modules/helper.py",
        }
    )

    class RedisConnection:
        async def mget(self, _keys):
            return [cache]

    async def redis_connection():
        return RedisConnection()

    monkeypatch.setattr(
        "src.services.workspace_release_storage.WorkspaceReleaseStorage",
        ReleaseStorage,
    )
    monkeypatch.setattr("src.services.repo_storage.RepoStorage", Repo)
    monkeypatch.setattr(
        "src.core.redis_client.get_redis_client",
        lambda: SimpleNamespace(_get_redis=redis_connection),
    )

    coherent, evidence = await inspect_workspace_release_coherence(
        descriptor,
        history_hashes={"modules/helper.py": expected_hash},
    )

    assert coherent is False
    assert evidence[0].immutable_coherent is False
    assert evidence[0].cache_coherent is True
    assert evidence[0].projected_repo_coherent is True
    assert evidence[0].history_coherent is True
