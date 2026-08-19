"""Focused compatibility and signed-history projection tests."""

from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from bifrost.workspace_release import (
    canonical_digest,
    workspace_manifest_id,
    workspace_registration_manifest_id,
)
from src.models.orm.workspace_promotions import (
    WorkspacePromotionArtifact,
    WorkspacePromotionRelease,
)
from src.services.platform_commit_writer import (
    PlatformCommitError,
    PlatformCommitSnapshot,
)
from src.services.workspace_release_projection import (
    WorkspaceReleaseProjectionError,
    WorkspaceReleaseProjectionPath,
    WorkspaceReleaseProjectionService,
    _ReleaseSuperseded,
    acquire_workspace_release_lock,
    classify_workspace_release_path,
)


def _hash(raw: bytes) -> str:
    import hashlib

    return hashlib.sha256(raw).hexdigest()


def _rows():
    organization_id = uuid4()
    first_base = b"VALUE = 'base'\n"
    first_target = b"VALUE = 'target'\n"
    second_base = b"OTHER = 'base'\n"
    second_target = b"OTHER = 'target'\n"
    paths = {
        "modules/first.py": (first_base, first_target),
        "modules/second.py": (second_base, second_target),
    }
    base_effective = {path: _hash(base) for path, (base, _target) in paths.items()}
    effective = {path: _hash(target) for path, (_base, target) in paths.items()}
    release_id = "sha256:" + "a" * 64
    now = datetime.now(timezone.utc)
    artifact = WorkspacePromotionArtifact(
        id=uuid4(),
        organization_id=organization_id,
        candidate_id="sha256:" + "b" * 64,
        content_id="sha256:" + "c" * 64,
        closure_id="sha256:" + "d" * 64,
        release_id=release_id,
        base_release_id="repo-v1:" + "e" * 64,
        base_manifest_id=workspace_manifest_id(base_effective),
        effective_manifest_id=workspace_manifest_id(effective),
        effective_registration_manifest_id=workspace_registration_manifest_id({}),
        registration_intent_fingerprint="sha256:" + "1" * 64,
        registration_state_fingerprint="sha256:" + "2" * 64,
        schema_version="bifrost.workspace-promotion-bundle/v2",
        target_kind="workspace",
        entity_type="workflow",
        entry_path="modules/first.py",
        entry_function="run",
        snapshot_id="sha256:" + "3" * 64,
        source_revision="4" * 40,
        source_tree_sha="5" * 40,
        source_artifact_key="source.zip",
        manifest_key="manifest.json",
        manifest={
            "schema_version": "bifrost.workspace-release-artifact/v1",
            "release_id": release_id,
            "effective_manifest_id": workspace_manifest_id(effective),
            "effective_files": effective,
            "effective_registration_manifest_id": workspace_registration_manifest_id(
                {}
            ),
            "effective_registrations": {},
            "protected_source": {"commit_sha": "4" * 40, "tree_sha": "5" * 40},
            "registration": {"state_fingerprint": "sha256:" + "2" * 64},
            "bounds": {
                "max_duration_seconds": 60,
                "max_external_calls": 10,
                "max_records_read": 100,
                "max_output_bytes": 1024,
            },
            "closure": [
                {
                    "path": path,
                    "sha256": effective[path],
                    "size": len(target),
                    "relation": "selected" if index == 0 else "dependency",
                }
                for index, (path, (_base, target)) in enumerate(paths.items())
            ],
        },
        risk_class="R0",
        disposition="review_required",
        artifact_state="eligible",
        policy_version="test",
        created_by=uuid4(),
        expires_at=now + timedelta(hours=1),
        created_at=now,
    )
    projection_paths = [
        {
            "path": path,
            "base_sha256": _hash(base),
            "target_sha256": _hash(target),
        }
        for path, (base, target) in paths.items()
    ]
    prepared_evidence = {"projection_paths": projection_paths}
    prepared_evidence["evidence_id"] = canonical_digest(prepared_evidence)
    activation_evidence = {
        "prepared_evidence_id": prepared_evidence["evidence_id"],
        "registration_actions": [],
        "projection_paths": {
            "projection_paths_id": canonical_digest(
                {
                    "schema": "bifrost.workspace-release-projection-paths/v1",
                    "paths": projection_paths,
                }
            ),
            "paths": projection_paths,
        },
    }
    activation_evidence["evidence_id"] = canonical_digest(activation_evidence)
    release = WorkspacePromotionRelease(
        id=uuid4(),
        organization_id=organization_id,
        artifact_id=artifact.id,
        activation_state="live",
        lock_state="queued",
        prepared_evidence=prepared_evidence,
        activation_evidence=activation_evidence,
        created_by=uuid4(),
    )
    return release, artifact, paths


def _add_inherited_path(release, artifact, paths):
    inherited_path = "shared/inherited.py"
    inherited_content = b"INHERITED = True\n"
    inherited_sha256 = _hash(inherited_content)
    effective = dict(artifact.manifest["effective_files"])
    effective[inherited_path] = inherited_sha256
    artifact.manifest = {
        **artifact.manifest,
        "effective_files": dict(sorted(effective.items())),
        "effective_manifest_id": workspace_manifest_id(effective),
    }
    artifact.effective_manifest_id = workspace_manifest_id(effective)
    base_effective = {path: _hash(base) for path, (base, _target) in paths.items()}
    base_effective[inherited_path] = inherited_sha256
    artifact.base_manifest_id = workspace_manifest_id(base_effective)
    return inherited_path, inherited_content


def _make_source_already_target(release, artifact, paths):
    projection_paths = [
        {
            "path": path,
            "base_sha256": _hash(target),
            "target_sha256": _hash(target),
        }
        for path, (_base, target) in paths.items()
    ]
    prepared = {"projection_paths": projection_paths}
    prepared["evidence_id"] = canonical_digest(prepared)
    activation = {
        "prepared_evidence_id": prepared["evidence_id"],
        "registration_actions": [{"intent": "preserve"}],
        "projection_paths": {
            "projection_paths_id": canonical_digest(
                {
                    "schema": "bifrost.workspace-release-projection-paths/v1",
                    "paths": projection_paths,
                }
            ),
            "paths": projection_paths,
        },
    }
    activation["evidence_id"] = canonical_digest(activation)
    release.prepared_evidence = prepared
    release.activation_evidence = activation
    artifact.base_manifest_id = artifact.effective_manifest_id


class Database:
    def __init__(self):
        self.commits = 0
        self.flushes = 0

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1


class Repo:
    def __init__(self, files):
        self.files = dict(files)

    async def list(self):
        return list(self.files)

    async def read_many(self, paths, **_kwargs):
        return {path: self.files[path] for path in paths}


class ReleaseStorage:
    def __init__(self, files):
        self.files = files

    async def read_many(self, paths):
        return {path: self.files[path] for path in paths}


class FileWriter:
    def __init__(self, repo):
        self.repo = repo
        self.writes = []

    async def write_file(self, path, content, **_kwargs):
        self.writes.append(path)
        self.repo.files[path] = content
        return SimpleNamespace(pending_deactivations=None)


class HistoryWriter:
    def __init__(self, hashes, *, fail_write=False):
        self.hashes = dict(hashes)
        self.fail_write = fail_write
        self.requests = []

    async def inspect(self, paths, **_kwargs):
        return PlatformCommitSnapshot(
            commit_sha="6" * 40,
            tree_sha="7" * 40,
            file_sha256={path: self.hashes.get(path) for path in paths},
            signature_state="VALID",
        )

    async def write(self, request):
        self.requests.append(request)
        if self.fail_write:
            raise PlatformCommitError("simulated Git failure")
        for item in request.files:
            self.hashes[item.path] = item.expected_sha256
        return SimpleNamespace(
            commit_sha="8" * 40,
            tree_sha="9" * 40,
            signature_state="VALID",
        )


@asynccontextmanager
async def _source_update(**_kwargs):
    yield


async def _coherent(hashes):
    return "generation-1", [
        SimpleNamespace(
            to_dict=lambda path=path, digest=digest: {
                "path": path,
                "durable_sha256": digest,
                "cache_sha256": digest,
                "cache_generation": "generation-1",
                "workspace_generation": "generation-1",
                "indexed": True,
                "coherent": True,
                "state": "coherent",
            }
        )
        for path, digest in hashes.items()
    ]


def test_path_classification_prefers_idempotent_target() -> None:
    path = WorkspaceReleaseProjectionPath("modules/a.py", "a" * 64, "b" * 64)

    assert classify_workspace_release_path(path, "a" * 64).disposition == "base"
    assert classify_workspace_release_path(path, "b" * 64).disposition == "target"
    assert classify_workspace_release_path(path, "c" * 64).disposition == "other"
    created = WorkspaceReleaseProjectionPath("modules/new.py", None, "d" * 64)
    assert classify_workspace_release_path(created, None).disposition == "base"


@pytest.mark.asyncio
async def test_activation_and_projection_share_one_global_advisory_lock() -> None:
    class LockDatabase:
        def __init__(self):
            self.calls = []

        async def execute(self, statement, params=None):
            self.calls.append((str(statement), params))

    db = LockDatabase()

    await acquire_workspace_release_lock(db, uuid4())
    await acquire_workspace_release_lock(db, uuid4())

    assert len(db.calls) == 2
    assert all(
        "hashtext('bifrost:workspace-release')" in sql and params is None
        for sql, params in db.calls
    )


@pytest.mark.asyncio
async def test_global_live_fence_rejects_multiple_live_rows() -> None:
    release, _artifact, _paths = _rows()

    class Result:
        class Scalars:
            def all(self):
                return [release.id, uuid4()]

        def scalars(self):
            return self.Scalars()

    class LiveDatabase:
        async def execute(self, _statement):
            return Result()

    service = WorkspaceReleaseProjectionService(
        LiveDatabase(), release.organization_id, commit_writer=None
    )

    with pytest.raises(WorkspaceReleaseProjectionError, match="More than one"):
        await service._ensure_still_live(release.id)


@pytest.mark.asyncio
async def test_job_for_non_live_release_marks_superseded_without_external_writes(
    monkeypatch,
) -> None:
    release, artifact, _paths = _rows()
    release.activation_state = "superseded"
    db = Database()
    monkeypatch.setattr(
        "src.services.workspace_release_projection.acquire_workspace_release_lock",
        AsyncMock(),
    )
    service = WorkspaceReleaseProjectionService(
        db,
        release.organization_id,
        commit_writer=None,
        release_storage_factory=lambda _prefix: pytest.fail(
            "immutable storage must not be read"
        ),
        file_storage_factory=lambda _db: pytest.fail("_repo must not be written"),
    )
    service._load_release = AsyncMock(return_value=(release, artifact))

    evidence = await service.lock_release(
        release.id, artifact.release_id, operator="operator@example.com"
    )

    assert evidence["state"] == "superseded"
    assert release.lock_state == "superseded"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_lock_projects_only_base_paths_and_records_signed_readback(
    monkeypatch,
) -> None:
    release, artifact, paths = _rows()
    first, second = paths
    repo = Repo({first: paths[first][0], second: paths[second][1]})
    history = HistoryWriter(
        {first: _hash(paths[first][0]), second: _hash(paths[second][1])}
    )
    file_writer = FileWriter(repo)
    db = Database()
    monkeypatch.setattr(
        "src.services.workspace_release_projection.acquire_workspace_release_lock",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.workspace_release_projection.workspace_source_update",
        _source_update,
    )
    service = WorkspaceReleaseProjectionService(
        db,
        release.organization_id,
        commit_writer=history,
        repo_storage=repo,
        release_storage_factory=lambda _prefix: ReleaseStorage(
            {path: target for path, (_base, target) in paths.items()}
        ),
        file_storage_factory=lambda _db: file_writer,
        coherence_inspector=_coherent,
    )
    service._load_release = AsyncMock(return_value=(release, artifact))
    service._ensure_still_live = AsyncMock()

    evidence = await service.lock_release(
        release.id, artifact.release_id, operator="operator@example.com"
    )

    assert release.lock_state == "locked"
    assert file_writer.writes == [first]
    assert len(history.requests) == 1
    assert [item.path for item in history.requests[0].files][0] == first
    assert [item.path for item in history.requests[0].files][1].startswith(
        ".bifrost/workspace-releases/ledger/"
    )
    assert history.requests[0].workspace_release_id == artifact.release_id
    assert evidence["history_after"]["signature_state"] == "VALID"
    assert evidence["repo_after_sha256"] == artifact.manifest["effective_files"]


@pytest.mark.asyncio
async def test_inherited_full_tree_mismatch_prevents_lock(monkeypatch) -> None:
    release, artifact, paths = _rows()
    inherited_path, inherited_content = _add_inherited_path(release, artifact, paths)
    target_files = {path: target for path, (_base, target) in paths.items()}
    target_files[inherited_path] = inherited_content
    repo = Repo({**target_files, inherited_path: b"STALE = True\n"})
    history = HistoryWriter(
        {path: _hash(content) for path, content in target_files.items()}
    )
    file_writer = FileWriter(repo)
    monkeypatch.setattr(
        "src.services.workspace_release_projection.acquire_workspace_release_lock",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.workspace_release_projection.workspace_source_update",
        _source_update,
    )
    service = WorkspaceReleaseProjectionService(
        Database(),
        release.organization_id,
        commit_writer=history,
        repo_storage=repo,
        release_storage_factory=lambda _prefix: ReleaseStorage(target_files),
        file_storage_factory=lambda _db: file_writer,
        coherence_inspector=_coherent,
    )
    service._load_release = AsyncMock(return_value=(release, artifact))
    service._ensure_still_live = AsyncMock()

    with pytest.raises(WorkspaceReleaseProjectionError, match="shared/inherited.py"):
        await service.lock_release(
            release.id, artifact.release_id, operator="operator@example.com"
        )

    assert release.lock_state == "attention_required"
    assert file_writer.writes == []
    assert history.requests == []


@pytest.mark.asyncio
async def test_registration_only_target_creates_one_signed_release_ledger(
    monkeypatch,
) -> None:
    release, artifact, paths = _rows()
    _make_source_already_target(release, artifact, paths)
    target_files = {path: target for path, (_base, target) in paths.items()}
    repo = Repo(target_files)
    history = HistoryWriter(
        {path: _hash(content) for path, content in target_files.items()}
    )
    file_writer = FileWriter(repo)
    monkeypatch.setattr(
        "src.services.workspace_release_projection.acquire_workspace_release_lock",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.workspace_release_projection.workspace_source_update",
        _source_update,
    )
    service = WorkspaceReleaseProjectionService(
        Database(),
        release.organization_id,
        commit_writer=history,
        repo_storage=repo,
        release_storage_factory=lambda _prefix: ReleaseStorage(target_files),
        file_storage_factory=lambda _db: file_writer,
        coherence_inspector=_coherent,
    )
    service._load_release = AsyncMock(return_value=(release, artifact))
    service._ensure_still_live = AsyncMock()

    evidence = await service.lock_release(
        release.id, artifact.release_id, operator="operator@example.com"
    )

    assert file_writer.writes == []
    assert len(history.requests) == 1
    request = history.requests[0]
    assert len(request.files) == 1
    ledger_file = request.files[0]
    assert ledger_file.path == evidence["release_ledger"]["path"]
    assert ledger_file.expected_before_sha256 is None
    assert ledger_file.expected_sha256 == evidence["release_ledger"]["sha256"]
    ledger = json.loads(base64.b64decode(ledger_file.content_base64))
    assert ledger["artifact_row_id"] == str(artifact.id)
    assert ledger["release_row_id"] == str(release.id)
    assert ledger["release_id"] == artifact.release_id
    assert ledger["effective_source_manifest_id"] == artifact.effective_manifest_id
    assert ledger["effective_registration_manifest_id"] == (
        artifact.effective_registration_manifest_id
    )
    assert ledger["registration_outcome"] == [{"intent": "preserve"}]
    assert request.expected_head_sha == "6" * 40
    assert request.expected_head_tree_sha == "7" * 40
    assert request.workspace_release_ledger_sha256 == ledger_file.expected_sha256
    assert evidence["history_after"]["signature_state"] == "VALID"


@pytest.mark.asyncio
async def test_divergence_fails_before_any_external_write(monkeypatch) -> None:
    release, artifact, paths = _rows()
    first, second = paths
    repo = Repo({first: b"unreviewed\n", second: paths[second][1]})
    history = HistoryWriter(
        {first: _hash(paths[first][0]), second: _hash(paths[second][1])}
    )
    file_writer = FileWriter(repo)
    db = Database()
    monkeypatch.setattr(
        "src.services.workspace_release_projection.acquire_workspace_release_lock",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.workspace_release_projection.workspace_source_update",
        _source_update,
    )
    service = WorkspaceReleaseProjectionService(
        db,
        release.organization_id,
        commit_writer=history,
        repo_storage=repo,
        release_storage_factory=lambda _prefix: ReleaseStorage(
            {path: target for path, (_base, target) in paths.items()}
        ),
        file_storage_factory=lambda _db: file_writer,
        coherence_inspector=_coherent,
    )
    service._load_release = AsyncMock(return_value=(release, artifact))
    service._ensure_still_live = AsyncMock()

    with pytest.raises(
        WorkspaceReleaseProjectionError, match="outside the immutable base/target"
    ):
        await service.lock_release(
            release.id, artifact.release_id, operator="operator@example.com"
        )

    assert file_writer.writes == []
    assert history.requests == []
    assert release.activation_state == "live"
    assert release.lock_state == "attention_required"
    assert release.error_code == "workspace_release_projection_diverged"


@pytest.mark.asyncio
async def test_git_failure_preserves_live_after_idempotent_repo_projection(
    monkeypatch,
) -> None:
    release, artifact, paths = _rows()
    repo = Repo({path: base for path, (base, _target) in paths.items()})
    history = HistoryWriter(
        {path: _hash(base) for path, (base, _target) in paths.items()},
        fail_write=True,
    )
    file_writer = FileWriter(repo)
    monkeypatch.setattr(
        "src.services.workspace_release_projection.acquire_workspace_release_lock",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.workspace_release_projection.workspace_source_update",
        _source_update,
    )
    service = WorkspaceReleaseProjectionService(
        Database(),
        release.organization_id,
        commit_writer=history,
        repo_storage=repo,
        release_storage_factory=lambda _prefix: ReleaseStorage(
            {path: target for path, (_base, target) in paths.items()}
        ),
        file_storage_factory=lambda _db: file_writer,
        coherence_inspector=_coherent,
    )
    service._load_release = AsyncMock(return_value=(release, artifact))
    service._ensure_still_live = AsyncMock()

    with pytest.raises(WorkspaceReleaseProjectionError, match="simulated Git failure"):
        await service.lock_release(
            release.id, artifact.release_id, operator="operator@example.com"
        )

    assert release.activation_state == "live"
    assert release.lock_state == "attention_required"
    assert release.error_code == "workspace_release_history_write_failed"
    assert repo.files == {path: target for path, (_base, target) in paths.items()}


@pytest.mark.asyncio
async def test_superseded_recheck_prevents_old_job_write(monkeypatch) -> None:
    release, artifact, paths = _rows()
    repo = Repo({path: base for path, (base, _target) in paths.items()})
    history = HistoryWriter(
        {path: _hash(base) for path, (base, _target) in paths.items()}
    )
    file_writer = FileWriter(repo)
    monkeypatch.setattr(
        "src.services.workspace_release_projection.acquire_workspace_release_lock",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.workspace_release_projection.workspace_source_update",
        _source_update,
    )
    service = WorkspaceReleaseProjectionService(
        Database(),
        release.organization_id,
        commit_writer=history,
        repo_storage=repo,
        release_storage_factory=lambda _prefix: ReleaseStorage(
            {path: target for path, (_base, target) in paths.items()}
        ),
        file_storage_factory=lambda _db: file_writer,
        coherence_inspector=_coherent,
    )
    service._load_release = AsyncMock(return_value=(release, artifact))
    service._ensure_still_live = AsyncMock(
        side_effect=[None, _ReleaseSuperseded("superseded")]
    )

    evidence = await service.lock_release(
        release.id, artifact.release_id, operator="operator@example.com"
    )

    assert evidence["state"] == "superseded"
    assert release.lock_state == "superseded"
    assert file_writer.writes == []
    assert history.requests == []
