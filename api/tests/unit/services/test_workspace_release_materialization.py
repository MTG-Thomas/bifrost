"""Focused immutable Workspace release preparation tests."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from bifrost.promotion import sha256_bytes
from bifrost.workspace_release import (
    repo_v1_release_id,
    workspace_closure_id,
    workspace_content_id,
    workspace_manifest_id,
    workspace_registration_manifest_id,
    workspace_release_id,
)
from src.models.orm.workspace_promotions import WorkspacePromotionArtifact
from src.services.workspace_promotions import _canonical_candidate
from src.services.workspace_release_materialization import (
    PREPARED_EVIDENCE_SCHEMA,
    WorkspaceReleaseMaterializer,
    WorkspaceReleasePreparationError,
    _compile_files,
    _read_closure_zip,
    isolated_candidate_import_smoke,
)


def _zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path, raw in files.items():
            archive.writestr(path, raw)
    return output.getvalue()


def _artifact(organization_id):
    base_files = {"modules/base.py": b"VALUE = 41\n"}
    closure_files = {
        "workflows/demo.py": (
            b"from modules.base import VALUE\ndef run():\n    return VALUE + 1\n"
        )
    }
    base_hashes = {path: sha256_bytes(raw) for path, raw in base_files.items()}
    closure_hashes = {path: sha256_bytes(raw) for path, raw in closure_files.items()}
    effective_hashes = dict(sorted({**base_hashes, **closure_hashes}.items()))
    entry = {"path": "workflows/demo.py", "function": "run"}
    closure_id = workspace_closure_id(entry, closure_hashes)
    content_id = workspace_content_id(entry, closure_id)
    registration_id = workspace_registration_manifest_id({})
    release_payload = {
        "organization_id": str(organization_id),
        "base_release_id": repo_v1_release_id(base_hashes),
        "base_manifest_id": workspace_manifest_id(base_hashes),
        "effective_manifest_id": workspace_manifest_id(effective_hashes),
        "effective_files": effective_hashes,
        "effective_registration_manifest_id": registration_id,
        "effective_registrations": {},
        "entry": entry,
        "registration_intent_fingerprint": "sha256:" + "1" * 64,
        "protected_source": {"commit_sha": "2" * 40, "tree_sha": "3" * 40},
    }
    release_id = workspace_release_id(release_payload)
    manifest = {
        "schema_version": "bifrost.workspace-release-artifact/v1",
        "organization_id": str(organization_id),
        "target": "production",
        "entry": entry,
        "content_id": content_id,
        "closure_id": closure_id,
        "release_id": release_id,
        **{
            key: value
            for key, value in release_payload.items()
            if key != "organization_id"
        },
        "snapshot_id": "sha256:" + "4" * 64,
        "closure": [
            {
                "path": "workflows/demo.py",
                "sha256": closure_hashes["workflows/demo.py"],
                "size": len(closure_files["workflows/demo.py"]),
                "relation": "selected",
            }
        ],
        "declared_effects": ["bifrost.read"],
        "static_effects": [],
        "computed_effects": ["bifrost.read"],
        "diagnostics": [],
        "bounds": {
            "max_duration_seconds": 30,
            "max_external_calls": 1,
            "max_records_read": 1,
            "max_output_bytes": 1000,
        },
        "requested_bounds": {},
        "registration": {
            "intent": [],
            "intent_fingerprint": "sha256:" + "1" * 64,
            "state": None,
            "state_fingerprint": "sha256:" + "5" * 64,
        },
        "local_run": None,
        "client": {
            "cli_version": "test",
            "sdk_version": "test",
            "contract_version": "2",
        },
        "policy_version": "test",
        "supersedes_candidate_id": None,
    }
    candidate_id = _canonical_candidate(manifest)
    artifact = WorkspacePromotionArtifact(
        id=uuid4(),
        organization_id=organization_id,
        candidate_id=candidate_id,
        content_id=content_id,
        closure_id=closure_id,
        release_id=release_id,
        base_release_id=release_payload["base_release_id"],
        base_manifest_id=release_payload["base_manifest_id"],
        effective_manifest_id=release_payload["effective_manifest_id"],
        effective_registration_manifest_id=registration_id,
        registration_intent_fingerprint="sha256:" + "1" * 64,
        registration_state_fingerprint="sha256:" + "5" * 64,
        schema_version="bifrost.workspace-promotion-bundle/v2",
        target_kind="workspace",
        entity_type="workflow",
        entry_path="workflows/demo.py",
        entry_function="run",
        snapshot_id="sha256:" + "4" * 64,
        source_revision="2" * 40,
        source_tree_sha="3" * 40,
        source_artifact_key="source.zip",
        manifest_key="manifest.json",
        manifest=manifest,
        risk_class="R0",
        disposition="review_required",
        artifact_state="review_required",
        policy_version="test",
        created_by=uuid4(),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        created_at=datetime.now(timezone.utc),
    )
    return artifact, base_files, closure_files


def test_compile_and_archive_readback_fail_closed() -> None:
    with pytest.raises(WorkspaceReleasePreparationError, match="compile failed"):
        _compile_files({"workflows/bad.py": b"def broken(:\n"})

    content = {"workflows/demo.py": b"def run(): return 1\n"}
    with pytest.raises(WorkspaceReleasePreparationError, match="hash mismatch"):
        _read_closure_zip(_zip(content), {"workflows/demo.py": "0" * 64})


@pytest.mark.asyncio
async def test_import_smoke_uses_only_candidate_tree() -> None:
    files = {
        "modules/base.py": b"VALUE = 41\n",
        "workflows/demo.py": (
            b"from modules.base import VALUE\ndef run():\n    return VALUE + 1\n"
        ),
    }

    result = await isolated_candidate_import_smoke(files, "workflows/demo.py", "run")

    assert result["source"] == "immutable_candidate_tree"
    assert result["function_callable"] is True

    with pytest.raises(
        WorkspaceReleasePreparationError, match="without `_repo` fallback"
    ):
        await isolated_candidate_import_smoke(
            {
                "workflows/demo.py": b"from modules.absent import VALUE\ndef run(): pass\n"
            },
            "workflows/demo.py",
            "run",
        )


@pytest.mark.asyncio
async def test_prepare_materializes_and_verifies_complete_effective_tree() -> None:
    organization_id = uuid4()
    artifact, base_files, closure_files = _artifact(organization_id)

    class Result:
        def __init__(self, *, scalar=None, rows=None):
            self.scalar = scalar
            self.rows = rows or []

        def scalar_one_or_none(self):
            return self.scalar

        def all(self):
            return self.rows

    class Database:
        def __init__(self):
            self.results = iter(
                [
                    Result(scalar=artifact),
                    Result(),
                    Result(rows=[]),
                    Result(),
                ]
            )
            self.added = None

        async def execute(self, _statement):
            return next(self.results)

        def add(self, value):
            self.added = value

        async def commit(self):
            return None

        async def refresh(self, value):
            if value.id is None:
                value.id = uuid4()

    class Repo:
        async def list(self):
            return list(base_files)

        async def read_many(self, paths, **_kwargs):
            return {path: base_files[path] for path in paths}

    class ContentStorage:
        source_artifact_key = "source.zip"

        async def read_source(self):
            return _zip(closure_files)

    class ReleaseStorage:
        def __init__(self):
            self.files = {}

        async def write_many(self, files):
            self.files.update(files)

        async def read_many(self, paths):
            return {path: self.files[path] for path in paths}

    release_storage = ReleaseStorage()

    async def smoke(files, path, function):
        assert files == {**base_files, **closure_files}
        return {
            "entry_path": path,
            "entry_function": function,
            "imported": True,
            "function_callable": True,
            "source": "immutable_candidate_tree",
        }

    db = Database()
    release, evidence = await WorkspaceReleaseMaterializer(
        db,
        organization_id,
        repo_storage=Repo(),
        artifact_storage_factory=lambda _org, _content: ContentStorage(),
        release_storage_factory=lambda _prefix: release_storage,
        smoke_runner=smoke,
    ).prepare(artifact.id, artifact.candidate_id, uuid4())

    assert release.activation_state == "prepared"
    assert evidence["schema_version"] == PREPARED_EVIDENCE_SCHEMA
    assert evidence["effective_files"] == artifact.manifest["effective_files"]
    assert release_storage.files == {**base_files, **closure_files}
    assert db.added is release


@pytest.mark.asyncio
async def test_draft_artifact_can_never_enter_prepare() -> None:
    organization_id = uuid4()
    artifact, _base, _closure = _artifact(organization_id)
    artifact.target_kind = "draft"

    class Result:
        def scalar_one_or_none(self):
            return artifact

    class Database:
        async def execute(self, _statement):
            return Result()

    class Repo:
        pass

    with pytest.raises(WorkspaceReleasePreparationError, match="local-only draft"):
        await WorkspaceReleaseMaterializer(
            Database(), organization_id, repo_storage=Repo()
        ).prepare(artifact.id, artifact.candidate_id, uuid4())
