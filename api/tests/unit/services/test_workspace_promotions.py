"""Focused safety policy tests for rapid Workspace promotion previews."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bifrost.promotion import snapshot_id
from bifrost.workspace_release import (
    workspace_registration_manifest_id,
    workspace_release_id,
)
from src.models.contracts.workspace_promotions import (
    PromotionClientContract,
    PromotionEntry,
    PromotionFile,
    PromotionSnapshot,
    WorkspacePromotionDraftRequest,
)
from src.services.workspace_promotions import (
    WorkspacePromotionInvalid,
    WorkspacePromotionPreviewService,
    _BaseSnapshot,
    _allocated_registration_id,
    _canonical_candidate,
    _canonical_impact_diagnostics,
    _closure_id,
    _content_id,
    _decode_draft_closure,
    _entry_metadata,
    _is_executable_python_path,
    _manifest_id,
    _repo_v1_release_id,
    _source_zip,
    _static_effects,
    _validate_draft_snapshot,
)


def test_candidate_hash_is_canonical_and_org_sensitive() -> None:
    first = _canonical_candidate({"organization_id": "a", "effects": ["bifrost.read"]})
    reordered = _canonical_candidate(
        {"effects": ["bifrost.read"], "organization_id": "a"}
    )
    other_org = _canonical_candidate(
        {"organization_id": "b", "effects": ["bifrost.read"]}
    )

    assert first == reordered
    assert first != other_org


@pytest.mark.asyncio
async def test_artifact_creation_uses_one_org_transaction_lock() -> None:
    organization_id = uuid4()
    calls = []

    class Database:
        async def execute(self, statement, parameters=None):
            calls.append((str(statement), parameters))

    service = WorkspacePromotionPreviewService(Database(), organization_id)

    await service._lock_artifact_creation()

    assert len(calls) == 1
    assert "pg_advisory_xact_lock" in calls[0][0]
    assert calls[0][1] == {"organization_id": str(organization_id)}


@pytest.mark.asyncio
async def test_reviewed_preview_cannot_supersede_an_inert_local_draft() -> None:
    artifact = SimpleNamespace(target_kind="draft")

    class Result:
        def scalar_one_or_none(self):
            return artifact

    class Database:
        async def execute(self, _statement, _parameters=None):
            return Result()

    service = WorkspacePromotionPreviewService(Database(), uuid4())

    with pytest.raises(WorkspacePromotionInvalid, match="local draft artifacts"):
        await service._resolve_superseded("sha256:" + "a" * 64)


def test_entry_requires_literal_effects_and_enforced_bounds() -> None:
    metadata = _entry_metadata(
        b"""
from bifrost import workflow

@workflow(
    name="Read things",
    effects=[
        WorkflowEffect(kind="integration.read", target="microsoft_graph"),
        WorkflowEffect(kind="bifrost.read"),
    ],
    enforced_bounds=WorkflowBounds(
        max_duration_seconds=30,
        max_external_calls=10,
        max_records_read=100,
        max_output_bytes=50000,
    ),
)
def read_things():
    return []
""",
        "features/demo.py",
        "read_things",
    )

    assert metadata["effects"] == [
        "bifrost.read",
        "integration.read:microsoft_graph",
    ]
    assert metadata["bounds"]["max_external_calls"] == 10


def test_static_classifier_finds_process_dynamic_network_and_secrets() -> None:
    effects, diagnostics = _static_effects(
        {
            "features/demo.py": (
                b"import httpx\nimport subprocess\nexec('pass')\n"
                b"api_key = 'definitely-a-real-looking-secret'\n"
            )
        }
    )

    assert effects == ["dynamic_code.execute", "network.unknown", "process.execute"]
    assert [item.code for item in diagnostics] == ["secret_material_detected"]


def test_source_archive_is_byte_deterministic() -> None:
    files = {"b.py": b"B = 2\n", "a.py": b"A = 1\n"}
    assert _source_zip(files) == _source_zip(dict(reversed(list(files.items()))))


async def test_autotask_shared_parser_regression_is_reverse_dependency_blocked() -> (
    None
):
    fixed_helper = (
        b"def ticket_id(payload): return payload.get('Id') or payload.get('id')\n"
    )
    stale_helper = b"def ticket_id(payload): return payload.get('id')\n"
    live = {
        "features/autotask/ticket_webhook.py": (
            b"from features.autotask._ticket_lifecycle import ticket_id\n"
        ),
        "features/cove/restore.py": (
            b"from features.autotask._ticket_lifecycle import ticket_id\n"
        ),
        "features/autotask/_ticket_lifecycle.py": fixed_helper,
    }
    candidate = {
        "features/cove/restore.py": live["features/cove/restore.py"],
        "features/autotask/_ticket_lifecycle.py": stale_helper,
    }
    diagnostics, closure = _canonical_impact_diagnostics(
        entry_path="features/cove/restore.py",
        base_files=live,
        closure_files=candidate,
    )

    assert len(diagnostics) == 1
    assert diagnostics[0].code == "shared_dependency_outside_candidate"
    assert diagnostics[0].path == "features/autotask/_ticket_lifecycle.py"
    assert "features/autotask/ticket_webhook.py" in diagnostics[0].message
    assert closure == set(candidate)


def test_content_identity_is_independent_of_review_attestation() -> None:
    entry = PromotionEntry(path="workflows/demo.py", function="demo")
    hashes = {"workflows/demo.py": "a" * 64}
    closure_id = _closure_id(entry, hashes)
    content_id = _content_id(entry, closure_id)

    assert content_id == _content_id(entry, closure_id)
    assert content_id != _content_id(entry, "sha256:" + "b" * 64)


def test_effective_manifest_and_repo_base_ids_bind_every_path() -> None:
    files = {
        "helpers/shared.py": "a" * 64,
        "workflows/demo.py": "b" * 64,
    }

    assert _manifest_id(files) == _manifest_id(dict(reversed(list(files.items()))))
    assert _repo_v1_release_id(files) == _repo_v1_release_id(
        dict(reversed(list(files.items())))
    )
    changed = {**files, "helpers/shared.py": "c" * 64}
    assert _manifest_id(changed) != _manifest_id(files)
    assert _repo_v1_release_id(changed) != _repo_v1_release_id(files)


def test_release_v1_executable_tree_excludes_generated_workspace_state() -> None:
    assert _is_executable_python_path("features/demo.py")
    assert _is_executable_python_path("modules/vendor.py")
    assert not _is_executable_python_path(".bifrost/workflows.yaml")
    assert not _is_executable_python_path(".git/config")
    assert not _is_executable_python_path("requirements.txt")
    assert not _is_executable_python_path("apps/demo/package.json")


def test_platform_allocated_registration_id_is_stable_and_path_scoped() -> None:
    organization_id = uuid4()

    first = _allocated_registration_id(organization_id, "workflows/demo.py", "demo")

    assert first == _allocated_registration_id(
        organization_id, "workflows/demo.py", "demo"
    )
    assert first != _allocated_registration_id(
        organization_id, "workflows/demo.py", "other"
    )


def test_registration_only_intent_changes_release_identity() -> None:
    files_id = "sha256:" + "1" * 64
    create = {
        "workflows/demo.py::demo": {
            "path": "workflows/demo.py",
            "function": "demo",
            "workflow_id": str(uuid4()),
            "type": "workflow",
            "name": "Demo",
            "organization_id": str(uuid4()),
            "is_active": True,
            "source_sha256": "a" * 64,
        }
    }
    absent_id = workspace_registration_manifest_id({})
    create_id = workspace_registration_manifest_id(create)
    common = {
        "organization_id": str(uuid4()),
        "base_release_id": "repo-v1:" + "2" * 64,
        "base_manifest_id": "sha256:" + "3" * 64,
        "effective_manifest_id": files_id,
        "effective_files": {"workflows/demo.py": "a" * 64},
        "entry": {"path": "workflows/demo.py", "function": "demo"},
        "protected_source": {"commit_sha": "4" * 40, "tree_sha": "5" * 40},
    }

    assert workspace_release_id(
        {
            **common,
            "effective_registration_manifest_id": absent_id,
            "effective_registrations": {},
        }
    ) != workspace_release_id(
        {
            **common,
            "effective_registration_manifest_id": create_id,
            "effective_registrations": create,
        }
    )


def test_draft_upload_decodes_only_a_bounded_exact_closure() -> None:
    source = b"def run():\n    return 1\n"
    digest = hashlib.sha256(source).hexdigest()
    files = {"workflows/demo.py": digest}
    request = WorkspacePromotionDraftRequest(
        schema_version="bifrost.workspace-draft-upload/v1",
        entry={"path": "workflows/demo.py", "function": "run"},
        snapshot={
            "snapshot_id": snapshot_id(files),
            "files": files,
            "closure": [
                {
                    "path": "workflows/demo.py",
                    "sha256": digest,
                    "content_base64": base64.b64encode(source).decode(),
                }
            ],
        },
        client={"cli_version": "test", "sdk_version": "test", "contract_version": "2"},
    )

    assert _decode_draft_closure(request) == {"workflows/demo.py": source}

    request.snapshot.closure[0].content_base64 = "not-base64"
    with pytest.raises(WorkspacePromotionInvalid, match="valid base64"):
        _decode_draft_closure(request)


def test_draft_snapshot_must_equal_server_base_plus_uploaded_closure() -> None:
    base_raw = b"VALUE = 'base'\n"
    draft_raw = b"def run():\n    return 1\n"
    base_hash = hashlib.sha256(base_raw).hexdigest()
    draft_hash = hashlib.sha256(draft_raw).hexdigest()
    files = {
        "modules/base.py": base_hash,
        "workflows/demo.py": draft_hash,
    }
    request = WorkspacePromotionDraftRequest(
        schema_version="bifrost.workspace-draft-upload/v1",
        entry=PromotionEntry(path="workflows/demo.py", function="run"),
        snapshot=PromotionSnapshot(
            snapshot_id=snapshot_id(files),
            files=files,
            closure=[
                PromotionFile(
                    path="workflows/demo.py",
                    sha256=draft_hash,
                    content_base64=base64.b64encode(draft_raw).decode(),
                )
            ],
        ),
        client=PromotionClientContract(
            cli_version="test", sdk_version="test", contract_version="2"
        ),
    )
    base = _BaseSnapshot(
        release_id="repo-v1:" + "a" * 64,
        manifest_id="sha256:" + "b" * 64,
        files={"modules/base.py": base_raw},
        hashes={"modules/base.py": base_hash},
        registrations={},
    )

    _validate_draft_snapshot(request, base, {"workflows/demo.py": draft_raw})

    request.snapshot.files["modules/base.py"] = "0" * 64
    with pytest.raises(WorkspacePromotionInvalid, match="server base plus closure"):
        _validate_draft_snapshot(request, base, {"workflows/demo.py": draft_raw})


@pytest.mark.asyncio
async def test_draft_artifact_is_local_only_expiring_and_content_reused(
    monkeypatch,
) -> None:
    source = b"""
from bifrost import workflow

@workflow(
    effects=[],
    enforced_bounds={
        "max_duration_seconds": 30,
        "max_external_calls": 1,
        "max_records_read": 1,
        "max_output_bytes": 1000,
    },
)
def run():
    return 1
"""
    digest = hashlib.sha256(source).hexdigest()
    files = {"workflows/demo.py": digest}
    request = WorkspacePromotionDraftRequest(
        schema_version="bifrost.workspace-draft-upload/v1",
        entry={"path": "workflows/demo.py", "function": "run"},
        snapshot={
            "snapshot_id": snapshot_id(files),
            "files": files,
            "closure": [
                {
                    "path": "workflows/demo.py",
                    "sha256": digest,
                    "content_base64": base64.b64encode(source).decode(),
                }
            ],
        },
        client={"cli_version": "test", "sdk_version": "test", "contract_version": "2"},
    )

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class Database:
        def __init__(self):
            self.artifact = None

        async def execute(self, _statement, _parameters=None):
            return Result(self.artifact)

        def add(self, value):
            self.artifact = value

        async def flush(self):
            if self.artifact.id is None:
                self.artifact.id = uuid4()
            if self.artifact.created_at is None:
                self.artifact.created_at = datetime.now(timezone.utc)

        async def commit(self):
            return None

        async def refresh(self, _value):
            return None

    class Storage:
        writes = 0

        def __init__(self, organization_id, content_id):
            self.source_artifact_key = (
                f"draft/{organization_id}/{content_id}/source.zip"
            )
            self.manifest_key = f"draft/{organization_id}/{content_id}/manifest.json"

        async def write_source(self, _content):
            type(self).writes += 1
            return self.source_artifact_key

        async def write_manifest(self, _content):
            return self.manifest_key

    async def audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "src.services.workspace_promotions.WorkspacePromotionArtifactStorage",
        Storage,
    )
    monkeypatch.setattr("src.services.workspace_promotions.emit_audit", audit)
    db = Database()
    base = _BaseSnapshot(
        release_id="repo-v1:" + "a" * 64,
        manifest_id="sha256:" + "b" * 64,
        files={},
        hashes={},
        registrations={},
    )
    service = WorkspacePromotionPreviewService(
        db, uuid4(), base_resolver=lambda: _async_value(base)
    )

    first = await service.upload_draft(request, uuid4())
    second = await service.upload_draft(request, uuid4())

    assert first.artifact_id == second.artifact_id
    assert first.authority == "local_only"
    assert first.activatable is False
    assert first.expires_at > first.created_at
    assert Storage.writes == 1
    assert db.artifact.target_kind == "draft"
    assert db.artifact.release_id is None
    assert db.artifact.source_revision is None
    assert db.artifact.registration_state_fingerprint is None


async def _async_value(value):
    return value
