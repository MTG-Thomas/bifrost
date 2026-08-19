"""Focused immutable Workspace release preparation tests."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
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
from src.models.orm.workspace_promotions import (
    WorkspacePromotionArtifact,
    WorkspacePromotionRelease,
)
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


async def _stable_generation() -> str:
    return "generation-1"


def _artifact(
    organization_id,
    *,
    risk_class="R0",
    computed_effects=None,
    include_affected_target=False,
):
    computed_effects = computed_effects or ["bifrost.read"]
    base_files = {"modules/base.py": b"VALUE = 41\n"}
    if include_affected_target:
        base_files["workflows/dependent.py"] = (
            b"from modules.base import VALUE\n"
            b"def dependent():\n    return VALUE\n"
        )
    closure_files = {
        "workflows/demo.py": (
            b"from modules.base import VALUE\ndef run():\n    return VALUE + 1\n"
        )
    }
    base_hashes = {path: sha256_bytes(raw) for path, raw in base_files.items()}
    closure_hashes = {path: sha256_bytes(raw) for path, raw in closure_files.items()}
    effective_hashes = dict(sorted({**base_hashes, **closure_hashes}.items()))
    governed_paths = sorted(closure_hashes)
    entry = {"path": "workflows/demo.py", "function": "run"}
    validation_targets = [
        {
            "path": "workflows/demo.py",
            "function": "run",
            "entity_type": "workflow",
            "relation": "selected_entry",
        }
    ]
    if include_affected_target:
        validation_targets.append(
            {
                "path": "workflows/dependent.py",
                "function": "dependent",
                "entity_type": "workflow",
                "relation": "affected_executable",
            }
        )
        validation_targets.sort(key=lambda item: (item["path"], item["function"]))
    closure_id = workspace_closure_id(entry, closure_hashes)
    content_id = workspace_content_id(entry, closure_id)
    registration_id = workspace_registration_manifest_id({})
    release_payload = {
        "organization_id": str(organization_id),
        "base_release_id": repo_v1_release_id(base_hashes),
        "base_manifest_id": workspace_manifest_id(base_hashes),
        "effective_manifest_id": workspace_manifest_id(effective_hashes),
        "effective_files": effective_hashes,
        "governed_paths": sorted(closure_hashes),
        "governed_manifest_id": workspace_manifest_id(
            {path: effective_hashes[path] for path in governed_paths}
        ),
        "effective_registration_manifest_id": registration_id,
        "effective_registrations": {},
        "entry": entry,
        "validation_targets": validation_targets,
        "risk_class": risk_class,
        "computed_effects": computed_effects,
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
        "validation_targets": validation_targets,
        "risk_class": risk_class,
        "declared_effects": computed_effects,
        "static_effects": [],
        "computed_effects": computed_effects,
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
        risk_class=risk_class,
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


def test_prepare_manifest_rejects_blockers_for_every_risk_class() -> None:
    artifact, _base, _closure = _artifact(
        uuid4(),
        risk_class="R2",
        computed_effects=["integration.write:halopsa"],
    )
    artifact.manifest["diagnostics"] = [
        {
            "code": "dynamic_import_unresolved",
            "severity": "blocker",
            "message": "computed import cannot be proven",
            "path": "workflows/demo.py",
        }
    ]
    artifact.candidate_id = _canonical_candidate(artifact.manifest)

    with pytest.raises(
        WorkspaceReleasePreparationError,
        match="blocker-free release",
    ):
        WorkspaceReleaseMaterializer._validate_manifest(
            artifact, artifact.manifest
        )


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
        workspace_generation=_stable_generation,
    ).prepare(artifact.id, artifact.candidate_id, uuid4())

    assert release.activation_state == "prepared"
    assert evidence["schema_version"] == PREPARED_EVIDENCE_SCHEMA
    assert evidence["effective_files"] == artifact.manifest["effective_files"]
    assert evidence["governed_paths"] == ["workflows/demo.py"]
    assert evidence["governed_manifest_id"] == artifact.manifest[
        "governed_manifest_id"
    ]
    assert [item["path"] for item in evidence["projection_paths"]] == [
        "workflows/demo.py"
    ]
    assert evidence["import_smoke"]["relation"] == "selected_entry"
    assert len(evidence["validation_smokes"]) == 1
    assert release_storage.files == {**base_files, **closure_files}
    assert db.added is release


@pytest.mark.asyncio
async def test_prepare_r2_smokes_every_bound_affected_executable() -> None:
    organization_id = uuid4()
    artifact, base_files, closure_files = _artifact(
        organization_id,
        risk_class="R2",
        computed_effects=["integration.write:halopsa"],
        include_affected_target=True,
    )

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
                [Result(scalar=artifact), Result(), Result(rows=[]), Result()]
            )

        async def execute(self, _statement):
            return next(self.results)

        def add(self, _value):
            pass

        async def commit(self):
            pass

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

    smoke_calls = []

    async def smoke(_files, path, function):
        smoke_calls.append((path, function))
        return {
            "entry_path": path,
            "entry_function": function,
            "imported": True,
            "function_callable": True,
            "source": "immutable_candidate_tree",
        }

    _release, evidence = await WorkspaceReleaseMaterializer(
        Database(),
        organization_id,
        repo_storage=Repo(),
        artifact_storage_factory=lambda _org, _content: ContentStorage(),
        release_storage_factory=lambda _prefix: ReleaseStorage(),
        smoke_runner=smoke,
        workspace_generation=_stable_generation,
    ).prepare(artifact.id, artifact.candidate_id, uuid4())

    assert smoke_calls == [
        ("workflows/demo.py", "run"),
        ("workflows/dependent.py", "dependent"),
    ]
    assert [item["relation"] for item in evidence["validation_smokes"]] == [
        "selected_entry",
        "affected_executable",
    ]
    assert evidence["import_smoke"] == evidence["validation_smokes"][0]


@pytest.mark.asyncio
async def test_active_base_is_current_repo_with_only_governed_live_overlay() -> None:
    organization_id = uuid4()
    base_artifact, original_repo, closure_files = _artifact(organization_id)
    live_release = WorkspacePromotionRelease(
        id=uuid4(),
        organization_id=organization_id,
        artifact_id=base_artifact.id,
        activation_state="live",
        lock_state="locked",
        created_by=uuid4(),
    )
    current_repo = {
        "modules/base.py": b"VALUE = 99\n",
        "workflows/demo.py": b"stale projected bytes\n",
        "modules/new_legacy.py": b"NEW = True\n",
    }
    immutable = {**original_repo, **closure_files}
    expected_hybrid = {
        **current_repo,
        "workflows/demo.py": closure_files["workflows/demo.py"],
    }
    target_artifact = SimpleNamespace(
        base_release_id=base_artifact.release_id,
        base_manifest_id=workspace_manifest_id(
            {
                path: sha256_bytes(raw)
                for path, raw in sorted(expected_hybrid.items())
            }
        ),
    )

    class Result:
        def all(self):
            return [(live_release, base_artifact)]

    class Database:
        async def execute(self, _statement):
            return Result()

    class Repo:
        async def list(self):
            return list(current_repo)

        async def read_many(self, paths, **_kwargs):
            return {path: current_repo[path] for path in paths}

    class ReleaseStorage:
        async def read_many(self, paths):
            return {path: immutable[path] for path in paths}

    materializer = WorkspaceReleaseMaterializer(
        Database(),
        organization_id,
        repo_storage=Repo(),
        release_storage_factory=lambda _prefix: ReleaseStorage(),
        workspace_generation=_stable_generation,
    )

    assert await materializer._base_files(target_artifact) == expected_hybrid


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
