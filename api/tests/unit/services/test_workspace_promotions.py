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
    WorkspacePromotionPreviewRequest,
)
from src.services.workspace_promotions import (
    R2_POLICY_DEFAULT_BOUNDS,
    UNDECLARED_EFFECT,
    WorkspacePromotionInvalid,
    WorkspacePromotionPreviewService,
    _BaseSnapshot,
    _allocated_registration_id,
    _canonical_candidate,
    _canonical_impact_diagnostics,
    _cohort_unregistered_diagnostics,
    _cumulative_governed_paths,
    _closure_id,
    _content_id,
    _decode_draft_closure,
    _entry_metadata,
    _existing_registration_is_rapid_eligible,
    _is_executable_python_path,
    _manifest_id,
    _promotion_risk_class,
    _registration_exposure,
    _repo_v1_release_id,
    _reconcile_effects,
    _risk_class_for_effects,
    _refresh_effective_registrations,
    _selected_effective_registration,
    _source_zip,
    _static_effects,
    _validation_targets,
    overlay_governed_base,
    read_generation_stable_executable_snapshot,
    _validate_draft_snapshot,
)

REQUIRED_R2_DEFAULT_KEYS = {
    "max_duration_seconds",
    "max_external_calls",
    "max_records_read",
    "max_output_bytes",
}


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


def test_cohort_paths_are_bound_into_closure_identity() -> None:
    entry = PromotionEntry(path="features/first.py", function="first")
    hashes = {"features/first.py": "a" * 64}

    single = _closure_id(entry, hashes)
    cohort = _closure_id(entry, hashes, ["features/first.py"])

    assert single != cohort


def test_multi_root_impact_uses_union_forward_closure() -> None:
    files = {
        "features/first.py": b"from helpers.shared import value\n",
        "features/second.py": b"from modules.vendor import call\n",
        "helpers/shared.py": b"value = 1\n",
        "modules/vendor.py": b"def call(): return 1\n",
    }

    diagnostics, forward, affected = _canonical_impact_diagnostics(
        entry_path="features/first.py",
        base_files={},
        closure_files=files,
        cohort_paths=["features/first.py", "features/second.py"],
    )

    assert forward == set(files)
    assert affected == set(files)
    assert all(item.severity != "blocker" for item in diagnostics)


def test_cohort_blocks_unregistered_decorated_sibling() -> None:
    targets = [
        SimpleNamespace(path="features/first.py", function="first"),
        SimpleNamespace(path="features/second.py", function="second"),
    ]

    diagnostics = _cohort_unregistered_diagnostics(
        targets,
        ["features/first.py", "features/second.py"],
        {"features/first.py::first"},
        "features/first.py::first",
    )

    assert [(item.code, item.path, item.severity) for item in diagnostics] == [
        ("cohort_entity_not_registered", "features/second.py", "blocker")
    ]


@pytest.mark.asyncio
async def test_declared_cohort_requires_exact_pending_source_release() -> None:
    organization_id = uuid4()
    release_id = uuid4()
    paths = {
        "features/first.py": "a" * 64,
        "features/second.py": "b" * 64,
    }
    record = SimpleNamespace(
        id=release_id,
        source_tree_sha="2" * 40,
        paths=paths,
        declared_disposition="pending",
        disposition="pending",
    )

    class Database:
        async def scalar(self, _statement):
            return record

    request = WorkspacePromotionPreviewRequest(
        schema_version="bifrost.workspace-promotion-bundle/v2",
        entry={"path": "features/first.py", "function": "first"},
        cohort_paths=sorted(paths),
        snapshot={
            "snapshot_id": snapshot_id(paths),
            "files": paths,
            "closure": [
                {"path": path, "sha256": digest}
                for path, digest in sorted(paths.items())
            ],
        },
        protected_source={"commit_sha": "1" * 40, "tree_sha": "2" * 40},
        client={"cli_version": "test", "sdk_version": "test", "contract_version": "11"},
    )

    result = await WorkspacePromotionPreviewService(
        Database(), organization_id
    )._validate_source_release_cohort(request)

    assert result.id == release_id


@pytest.mark.asyncio
async def test_declared_cohort_reports_exact_path_mismatch() -> None:
    record = SimpleNamespace(
        id=uuid4(),
        source_tree_sha="2" * 40,
        paths={"features/other.py": "b" * 64},
        declared_disposition="pending",
        disposition="pending",
    )

    class Database:
        async def scalar(self, _statement):
            return record

    path = "features/first.py"
    hashes = {path: "a" * 64}
    request = WorkspacePromotionPreviewRequest(
        schema_version="bifrost.workspace-promotion-bundle/v2",
        entry={"path": path, "function": "first"},
        cohort_paths=[path],
        snapshot={
            "snapshot_id": snapshot_id(hashes),
            "files": hashes,
            "closure": [{"path": path, "sha256": "a" * 64}],
        },
        protected_source={"commit_sha": "1" * 40, "tree_sha": "2" * 40},
        client={"cli_version": "test", "sdk_version": "test", "contract_version": "11"},
    )

    with pytest.raises(
        WorkspacePromotionInvalid, match="missing=.*other.*extra=.*first"
    ):
        await WorkspacePromotionPreviewService(
            Database(), uuid4()
        )._validate_source_release_cohort(request)


def test_existing_workflow_can_preserve_exposure() -> None:
    registration = SimpleNamespace(
        organization_id=None,
        type="workflow",
        endpoint_enabled=True,
        public_endpoint=True,
        api_key_enabled=True,
        access_level="authenticated",
        is_active=True,
        roles=[],
    )

    assert _existing_registration_is_rapid_eligible(registration) is True


def test_non_workflow_registration_remains_ineligible_for_rapid_promotion() -> None:
    registration = SimpleNamespace(
        organization_id=None,
        type="workflow",
        endpoint_enabled=False,
        public_endpoint=False,
        api_key_enabled=False,
        access_level="role_based",
        is_active=True,
        roles=[],
    )
    registration.type = "tool"

    assert _existing_registration_is_rapid_eligible(registration) is False


def test_inactive_exposed_registration_cannot_be_reactivated_by_source_release() -> (
    None
):
    registration = SimpleNamespace(
        type="workflow",
        endpoint_enabled=True,
        public_endpoint=True,
        api_key_enabled=False,
        access_level="authenticated",
        is_active=False,
        roles=[],
    )

    assert _existing_registration_is_rapid_eligible(registration) is False


def test_preserved_activation_surface_forces_r2_without_blocking_source() -> None:
    state = {
        "access_level": "authenticated",
        "role_ids": [],
        "endpoint_enabled": True,
        "public_endpoint": False,
        "api_key_enabled": True,
    }

    assert _registration_exposure(state) == state
    assert _promotion_risk_class(["bifrost.read"], [state]) == "R2"
    assert _promotion_risk_class(["bifrost.read"], []) == "R0"


def test_shared_source_file_uses_highest_registration_risk() -> None:
    registrations = [
        {
            "path": "workflows/shared.py",
            "function": "selected",
            "access_level": "role_based",
            "role_ids": [],
            "endpoint_enabled": False,
            "public_endpoint": False,
            "api_key_enabled": False,
        },
        {
            "path": "workflows/shared.py",
            "function": "existing_endpoint",
            "access_level": "authenticated",
            "role_ids": [],
            "endpoint_enabled": True,
            "public_endpoint": False,
            "api_key_enabled": False,
        },
    ]

    assert _promotion_risk_class(["bifrost.read"], registrations) == "R2"


@pytest.mark.asyncio
async def test_live_base_enriches_legacy_registration_with_current_exposure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    workflow_id = uuid4()
    role_id = uuid4()
    descriptor = SimpleNamespace(
        effective_registrations={
            "workflows/shared.py::run": {
                "path": "workflows/shared.py",
                "function": "run",
                "workflow_id": str(workflow_id),
                "type": "workflow",
                "name": "Shared",
                "organization_id": str(organization_id),
                "is_active": True,
                "source_sha256": "a" * 64,
                "runtime_bounds": {"max_duration_seconds": 30},
            }
        }
    )
    current = SimpleNamespace(
        id=workflow_id,
        name="Shared",
        type="workflow",
        is_active=True,
        organization_id=organization_id,
        endpoint_enabled=True,
        public_endpoint=False,
        api_key_enabled=True,
        access_level="authenticated",
        roles=[SimpleNamespace(id=role_id)],
    )

    async def find_current(*_args, **_kwargs):
        return current

    monkeypatch.setattr(
        "src.services.workspace_promotions.find_workspace_workflow",
        find_current,
    )
    service = WorkspacePromotionPreviewService(
        SimpleNamespace(),
        organization_id,
        repo_storage=SimpleNamespace(),
    )

    registrations = await service._current_registration_snapshot(descriptor)

    assert registrations["workflows/shared.py::run"] == {
        **descriptor.effective_registrations["workflows/shared.py::run"],
        "access_level": "authenticated",
        "role_ids": [str(role_id)],
        "endpoint_enabled": True,
        "public_endpoint": False,
        "api_key_enabled": True,
    }


@pytest.mark.asyncio
async def test_live_base_rejects_exposure_drift_from_explicit_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    workflow_id = uuid4()
    descriptor = SimpleNamespace(
        effective_registrations={
            "workflows/shared.py::run": {
                "path": "workflows/shared.py",
                "function": "run",
                "workflow_id": str(workflow_id),
                "type": "workflow",
                "name": "Shared",
                "organization_id": str(organization_id),
                "is_active": True,
                "source_sha256": "a" * 64,
                "runtime_bounds": {"max_duration_seconds": 30},
                "access_level": "role_based",
                "role_ids": [],
                "endpoint_enabled": False,
                "public_endpoint": False,
                "api_key_enabled": False,
            }
        }
    )
    current = SimpleNamespace(
        id=workflow_id,
        name="Shared",
        type="workflow",
        is_active=True,
        organization_id=organization_id,
        endpoint_enabled=True,
        public_endpoint=False,
        api_key_enabled=False,
        access_level="authenticated",
        roles=[],
    )

    async def find_current(*_args, **_kwargs):
        return current

    monkeypatch.setattr(
        "src.services.workspace_promotions.find_workspace_workflow",
        find_current,
    )
    service = WorkspacePromotionPreviewService(
        SimpleNamespace(),
        organization_id,
        repo_storage=SimpleNamespace(),
    )

    with pytest.raises(WorkspacePromotionInvalid, match="exposure changed"):
        await service._current_registration_snapshot(descriptor)


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


def test_legacy_undeclared_effects_are_bound_as_conservative_r2() -> None:
    metadata = _entry_metadata(
        b"""
from bifrost import workflow

@workflow(name="Legacy webhook")
async def legacy_webhook(payload=None):
    return payload
""",
        "features/legacy.py",
        "legacy_webhook",
    )

    computed, undeclared = _reconcile_effects(
        metadata["effects"],
        [],
        effects_declared=metadata["effects_declared"],
    )

    assert metadata["effects"] == []
    assert metadata["effects_declared"] is False
    assert computed == [UNDECLARED_EFFECT]
    assert undeclared == []
    assert _risk_class_for_effects(computed) == "R2"
    assert REQUIRED_R2_DEFAULT_KEYS == set(R2_POLICY_DEFAULT_BOUNDS)


def test_explicit_effects_cannot_be_downgraded_by_undeclared_classification() -> None:
    r0, _ = _reconcile_effects(["bifrost.read"], [], effects_declared=True)
    r1, _ = _reconcile_effects(["integration.read:meraki"], [], effects_declared=True)

    assert UNDECLARED_EFFECT not in r0
    assert UNDECLARED_EFFECT not in r1
    assert _risk_class_for_effects(r0) == "R0"
    assert _risk_class_for_effects(r1) == "R1"


@pytest.mark.asyncio
async def test_reviewed_preview_accepts_legacy_undeclared_r2_without_local_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"""
from bifrost import workflow

@workflow(name="Legacy webhook")
async def legacy_webhook(payload=None):
    return payload
"""
    path = "features/legacy.py"
    source_hash = hashlib.sha256(source).hexdigest()
    commit_sha = "1" * 40
    tree_sha = "2" * 40
    organization_id = uuid4()

    class Result:
        def scalar_one_or_none(self):
            return None

    class Database:
        def __init__(self):
            self.added = None

        async def execute(self, _statement, _parameters=None):
            return Result()

        def add(self, value):
            self.added = value

        async def flush(self):
            if self.added.id is None:
                self.added.id = uuid4()

        async def commit(self):
            pass

        async def refresh(self, _value):
            pass

    class CommitWriter:
        async def read_files(self, paths, *, ref):
            assert paths == (path,)
            assert ref == "main"
            return SimpleNamespace(
                commit_sha=commit_sha,
                tree_sha=tree_sha,
                files={path: source},
            )

    class Storage:
        def __init__(self, _organization_id, _content_id):
            self.source_artifact_key = "source.zip"
            self.manifest_key = "manifest.json"

        async def write_source(self, _raw):
            return self.source_artifact_key

        async def write_manifest(self, _raw):
            return self.manifest_key

    async def base_resolver():
        return _BaseSnapshot(
            release_id=_repo_v1_release_id({}),
            manifest_id=_manifest_id({}),
            files={},
            hashes={},
            registrations={},
        )

    async def plan_registrations(*_args, **_kwargs):
        return (
            [
                {
                    "action": "create",
                    "path": path,
                    "function_name": "legacy_webhook",
                    "type": "workflow",
                    "name": "Legacy webhook",
                    "requested_id": str(uuid4()),
                    "organization_id": str(organization_id),
                }
            ],
            [],
        )

    async def no_existing(*_args, **_kwargs):
        return None

    async def no_active(*_args, **_kwargs):
        return []

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "src.services.workspace_promotions.plan_workspace_registrations",
        plan_registrations,
    )
    monkeypatch.setattr(
        "src.services.workspace_promotions.find_workspace_workflow",
        no_existing,
    )
    monkeypatch.setattr(
        "src.services.workspace_promotions.list_active_workspace_workflows",
        no_active,
    )
    monkeypatch.setattr(
        "src.services.workspace_promotions.emit_audit",
        no_audit,
    )
    monkeypatch.setattr(
        "src.services.workspace_promotions.WorkspacePromotionArtifactStorage",
        Storage,
    )
    request = WorkspacePromotionPreviewRequest(
        schema_version="bifrost.workspace-promotion-bundle/v2",
        entry={"path": path, "function": "legacy_webhook"},
        snapshot={
            "snapshot_id": snapshot_id({path: source_hash}),
            "files": {path: source_hash},
            "closure": [{"path": path, "sha256": source_hash}],
        },
        protected_source={"commit_sha": commit_sha, "tree_sha": tree_sha},
        client={"cli_version": "test", "sdk_version": "test", "contract_version": "2"},
    )

    response = await WorkspacePromotionPreviewService(
        Database(),
        organization_id,
        commit_writer=CommitWriter(),
        base_resolver=base_resolver,
    ).preview(request, uuid4())

    assert response.risk_class == "R2"
    assert response.declared_effects == []
    assert response.computed_effects == [UNDECLARED_EFFECT]
    assert response.bounds == {}
    registration = response.effective_registrations[f"{path}::legacy_webhook"]
    assert registration["runtime_bounds"] == R2_POLICY_DEFAULT_BOUNDS
    diagnostics = {item.code: item for item in response.diagnostics}
    assert diagnostics["effects_undeclared"].severity == "warning"
    assert diagnostics["local_run_evidence_missing"].severity == "warning"
    assert diagnostics["r2_policy_default_runtime_bounds"].severity == "warning"
    assert all(item.severity != "blocker" for item in response.diagnostics)


@pytest.mark.asyncio
async def test_exact_byte_preview_binds_every_active_sibling_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = "features/ninjaone/workflows/device_automation.py"
    selected_id = uuid4()
    sibling_id = uuid4()
    organization_id = uuid4()
    source = """
from bifrost import workflow

@workflow(
    id="__SELECTED_ID__",
    name="Inspect device",
    effects=[{"kind": "integration.read", "target": "ninjaone"}],
    enforced_bounds={
        "max_duration_seconds": 30,
        "max_external_calls": 5,
        "max_records_read": 100,
        "max_output_bytes": 4096,
    },
)
async def inspect_device():
    return {}

@workflow(name="Run device batch")
async def run_device_batch():
    return {}
"""
    source = source.replace("__SELECTED_ID__", str(selected_id)).encode()
    source_hash = hashlib.sha256(source).hexdigest()
    commit_sha = "3" * 40
    tree_sha = "4" * 40

    def workflow_row(
        workflow_id,
        function_name,
        name,
    ):
        return SimpleNamespace(
            id=workflow_id,
            organization_id=organization_id,
            solution_id=None,
            path=path,
            function_name=function_name,
            name=name,
            type="workflow",
            is_active=True,
            endpoint_enabled=False,
            public_endpoint=False,
            api_key_enabled=False,
            access_level="role_based",
            roles=[],
        )

    selected = workflow_row(selected_id, "inspect_device", "Inspect device")
    sibling = workflow_row(sibling_id, "run_device_batch", "Run device batch")

    class Result:
        def scalar_one_or_none(self):
            return None

    class Database:
        def __init__(self):
            self.added = None

        async def execute(self, _statement, _parameters=None):
            return Result()

        def add(self, value):
            self.added = value

        async def flush(self):
            if self.added.id is None:
                self.added.id = uuid4()

        async def commit(self):
            return None

        async def refresh(self, _value):
            return None

    class CommitWriter:
        async def read_files(self, paths, *, ref):
            assert paths == (path,)
            assert ref == "main"
            return SimpleNamespace(
                commit_sha=commit_sha,
                tree_sha=tree_sha,
                files={path: source},
            )

    class Storage:
        def __init__(self, _organization_id, _content_id):
            self.source_artifact_key = "source.zip"
            self.manifest_key = "manifest.json"

        async def write_source(self, _raw):
            return self.source_artifact_key

        async def write_manifest(self, _raw):
            return self.manifest_key

    base_release_id = "sha256:" + "b" * 64

    async def base_resolver():
        return _BaseSnapshot(
            release_id=base_release_id,
            manifest_id=_manifest_id({path: source_hash}),
            files={path: source},
            hashes={path: source_hash},
            registrations={},
            governed_paths=(path,),
        )

    async def plan_registrations(*_args, **_kwargs):
        return (
            [
                {
                    "action": "preserve",
                    "path": path,
                    "function_name": "inspect_device",
                    "type": "workflow",
                    "name": "Inspect device",
                    "requested_id": str(selected_id),
                    "organization_id": str(organization_id),
                }
            ],
            [],
        )

    async def existing(*_args, **_kwargs):
        return selected

    async def active(*_args, **_kwargs):
        return [selected, sibling]

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "src.services.workspace_promotions.plan_workspace_registrations",
        plan_registrations,
    )
    monkeypatch.setattr(
        "src.services.workspace_promotions.find_workspace_workflow",
        existing,
    )
    monkeypatch.setattr(
        "src.services.workspace_promotions.list_active_workspace_workflows",
        active,
    )
    monkeypatch.setattr("src.services.workspace_promotions.emit_audit", no_audit)
    monkeypatch.setattr(
        "src.services.workspace_promotions.WorkspacePromotionArtifactStorage",
        Storage,
    )
    request = WorkspacePromotionPreviewRequest(
        schema_version="bifrost.workspace-promotion-bundle/v2",
        entry={"path": path, "function": "inspect_device"},
        snapshot={
            "snapshot_id": snapshot_id({path: source_hash}),
            "files": {path: source_hash},
            "closure": [{"path": path, "sha256": source_hash}],
        },
        protected_source={"commit_sha": commit_sha, "tree_sha": tree_sha},
        expected_base_release_id=base_release_id,
        client={"cli_version": "test", "sdk_version": "test", "contract_version": "2"},
    )

    response = await WorkspacePromotionPreviewService(
        Database(),
        organization_id,
        commit_writer=CommitWriter(),
        base_resolver=base_resolver,
    ).preview(request, uuid4())

    assert response.release_id != base_release_id
    assert response.effective_manifest_id == _manifest_id({path: source_hash})
    assert response.risk_class == "R2"
    assert response.computed_effects == [
        "integration.read:ninjaone",
        UNDECLARED_EFFECT,
    ]
    assert set(response.effective_registrations) == {
        f"{path}::inspect_device",
        f"{path}::run_device_batch",
    }
    preserved = response.effective_registrations[f"{path}::run_device_batch"]
    assert preserved["workflow_id"] == str(sibling_id)
    assert preserved["runtime_bounds"] == R2_POLICY_DEFAULT_BOUNDS
    diagnostics = {item.code: item for item in response.diagnostics}
    assert diagnostics["active_sibling_registration_bound"].severity == "info"
    assert diagnostics["active_sibling_effects_undeclared"].severity == "warning"
    assert diagnostics["active_sibling_policy_bounds"].severity == "warning"
    assert all(item.severity != "blocker" for item in response.diagnostics)


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


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        (["integration.read:ninjaone"], ["integration.read:ninjaone"]),
        (["integration.write:halopsa"], ["integration.write:halopsa"]),
        (["network.read"], ["network.read"]),
        (["network.write"], ["network.write"]),
    ],
)
def test_precise_external_effect_covers_ambiguous_network_import(
    declared: list[str], expected: list[str]
) -> None:
    computed, undeclared = _reconcile_effects(declared, ["network.unknown"])

    assert computed == expected
    assert undeclared == []


def test_ambiguous_network_import_without_external_declaration_stays_blocked() -> None:
    computed, undeclared = _reconcile_effects(["bifrost.read"], ["network.unknown"])

    assert computed == ["bifrost.read", "network.unknown"]
    assert undeclared == ["network.unknown"]


def test_integration_declaration_does_not_cover_other_static_effects() -> None:
    computed, undeclared = _reconcile_effects(
        ["integration.read:ninjaone"],
        ["network.unknown", "process.execute"],
    )

    assert computed == ["integration.read:ninjaone", "process.execute"]
    assert undeclared == ["process.execute"]


def test_ninja_dependency_network_import_is_covered_by_integration_read() -> None:
    static_effects, diagnostics = _static_effects(
        {
            "features/ninjaone/workflows/inventory.py": (
                b"from modules.ninjaone import NinjaOneClient\n"
            ),
            "modules/ninjaone/api.py": b"import requests\n",
        }
    )

    computed, undeclared = _reconcile_effects(
        ["integration.read:ninjaone"], static_effects
    )

    assert diagnostics == []
    assert static_effects == ["network.unknown"]
    assert computed == ["integration.read:ninjaone"]
    assert undeclared == []
    assert _risk_class_for_effects(computed) == "R1"


def test_source_archive_is_byte_deterministic() -> None:
    files = {"b.py": b"B = 2\n", "a.py": b"A = 1\n"}
    assert _source_zip(files) == _source_zip(dict(reversed(list(files.items()))))


async def test_autotask_shared_parser_reverse_dependents_become_validation_targets() -> (
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
    diagnostics, closure, affected = _canonical_impact_diagnostics(
        entry_path="features/cove/restore.py",
        base_files=live,
        closure_files=candidate,
    )

    assert len(diagnostics) == 1
    assert diagnostics[0].code == "reverse_dependents_bound"
    assert diagnostics[0].severity == "info"
    assert diagnostics[0].path == "features/autotask/_ticket_lifecycle.py"
    assert "features/autotask/ticket_webhook.py" in diagnostics[0].message
    assert closure == set(candidate)
    assert affected == set(live)


def test_affected_executable_targets_are_canonical_and_entry_bound() -> None:
    files = {
        "modules/shared.py": b"VALUE = 1\n",
        "workflows/entry.py": b"""
from bifrost import workflow
from modules.shared import VALUE
@workflow(effects=[WorkflowEffect(kind="bifrost.read")])
def entry(): return VALUE
""",
        "workflows/consumer.py": b"""
from bifrost import workflow
from modules.shared import VALUE
@workflow(effects=[WorkflowEffect(kind="integration.write", target="halo")])
def consumer(): return VALUE
""",
    }

    targets = _validation_targets(
        files,
        set(files),
        PromotionEntry(path="workflows/entry.py", function="entry"),
    )

    assert [item.model_dump() for item in targets] == [
        {
            "path": "workflows/consumer.py",
            "function": "consumer",
            "entity_type": "workflow",
            "relation": "affected_executable",
        },
        {
            "path": "workflows/entry.py",
            "function": "entry",
            "entity_type": "workflow",
            "relation": "selected_entry",
        },
    ]


def test_affected_graph_uncertainty_remains_fail_closed() -> None:
    files = {
        "modules/shared.py": b"VALUE = 1\n",
        "workflows/one.py": b"@workflow(name='Child')\ndef one(): return 1\n",
        "workflows/two.py": b"@workflow(name='Child')\ndef two(): return 2\n",
        "workflows/parent.py": (
            b"from importlib import import_module\n"
            b"from modules.shared import VALUE\n"
            b"from modules.missing import ABSENT\n"
            b"async def parent(name):\n"
            b"    import_module(name)\n"
            b"    await sdk.workflows.execute(function_name=name)\n"
            b"    return {'workflow_name': 'Child', 'value': VALUE}\n"
        ),
    }

    diagnostics, _forward, _affected = _canonical_impact_diagnostics(
        entry_path="workflows/parent.py",
        base_files=files,
        closure_files=files,
    )

    blockers = {item.code for item in diagnostics if item.severity == "blocker"}
    assert {
        "unresolved_repo_import",
        "ambiguous_workflow_reference",
        "dynamic_import_unresolved",
        "dynamic_workflow_reference_unresolved",
    } <= blockers


def test_reviewed_risk_classes_keep_read_and_write_effects_explicit() -> None:
    assert _risk_class_for_effects(["bifrost.read"]) == "R0"
    assert (
        _risk_class_for_effects(["bifrost.read", "integration.read:microsoft_graph"])
        == "R1"
    )
    assert _risk_class_for_effects(["integration.write:halopsa"]) == "R2"


def test_selected_registration_binds_its_own_runtime_bounds() -> None:
    registration = _selected_effective_registration(
        entry=PromotionEntry(path="workflows/demo.py", function="demo"),
        action={
            "requested_id": "11111111-1111-1111-1111-111111111111",
            "type": "workflow",
            "name": "Demo",
            "organization_id": "22222222-2222-2222-2222-222222222222",
        },
        registration_state=None,
        source_sha256="a" * 64,
        runtime_bounds={
            "max_records_read": 100,
            "max_duration_seconds": 30,
            "max_output_bytes": 1000,
            "max_external_calls": 5,
        },
    )

    assert registration["runtime_bounds"] == {
        "max_duration_seconds": 30,
        "max_external_calls": 5,
        "max_output_bytes": 1000,
        "max_records_read": 100,
    }
    assert registration["access_level"] == "role_based"
    assert registration["role_ids"] == []
    assert registration["endpoint_enabled"] is False
    assert registration["public_endpoint"] is False
    assert registration["api_key_enabled"] is False


def test_changed_multi_entity_file_refreshes_every_effective_registration() -> None:
    path = "workflows/shared.py"
    raw = b"""
from bifrost import workflow
@workflow(
    name="First",
    effects=[WorkflowEffect(kind="bifrost.read")],
    enforced_bounds={
        "max_duration_seconds": 10,
        "max_external_calls": 1,
        "max_records_read": 20,
        "max_output_bytes": 300,
    },
)
def first(): return 1
@workflow(
    name="Second",
    effects=[WorkflowEffect(kind="integration.write", target="halo")],
    enforced_bounds={
        "max_duration_seconds": 40,
        "max_external_calls": 5,
        "max_records_read": 60,
        "max_output_bytes": 700,
    },
)
def second(): return 2
"""
    new_hash = hashlib.sha256(raw).hexdigest()
    first_key = f"{path}::first"
    second_key = f"{path}::second"
    base = {
        first_key: {
            "path": path,
            "function": "first",
            "workflow_id": str(uuid4()),
            "type": "workflow",
            "name": "First",
            "organization_id": str(uuid4()),
            "is_active": True,
            "source_sha256": "a" * 64,
            "runtime_bounds": {"max_duration_seconds": 1},
        },
        second_key: {
            "path": path,
            "function": "second",
            "workflow_id": str(uuid4()),
            "type": "workflow",
            "name": "Second",
            "organization_id": str(uuid4()),
            "is_active": True,
            "source_sha256": "a" * 64,
            "runtime_bounds": {"max_duration_seconds": 1},
        },
    }
    selected = {
        **base[first_key],
        "source_sha256": new_hash,
        "runtime_bounds": {
            "max_duration_seconds": 10,
            "max_external_calls": 1,
            "max_records_read": 20,
            "max_output_bytes": 300,
        },
    }
    diagnostics = []

    result = _refresh_effective_registrations(
        base_registrations=base,
        closure_files={path: raw},
        closure_hashes={path: new_hash},
        selected_key=first_key,
        selected_registration=selected,
        risk_class="R0",
        diagnostics=diagnostics,
    )

    assert diagnostics == []
    assert result[first_key]["source_sha256"] == new_hash
    assert result[second_key]["source_sha256"] == new_hash
    assert result[first_key]["runtime_bounds"]["max_duration_seconds"] == 10
    assert result[second_key]["runtime_bounds"] == {
        "max_duration_seconds": 40,
        "max_external_calls": 5,
        "max_output_bytes": 700,
        "max_records_read": 60,
    }
    assert result[second_key]["workflow_id"] == base[second_key]["workflow_id"]


def test_changed_multi_entity_file_blocks_non_selected_metadata_drift() -> None:
    path = "workflows/shared.py"
    raw = b"""
from bifrost import workflow
@workflow(
    name="Renamed Outside Selection",
    enforced_bounds={
        "max_duration_seconds": 10,
        "max_external_calls": 1,
        "max_records_read": 20,
        "max_output_bytes": 300,
    },
)
def second(): return 2
"""
    key = f"{path}::second"
    base = {
        key: {
            "path": path,
            "function": "second",
            "workflow_id": str(uuid4()),
            "type": "workflow",
            "name": "Second",
            "organization_id": str(uuid4()),
            "is_active": True,
            "source_sha256": "a" * 64,
            "runtime_bounds": {"max_duration_seconds": 1},
        }
    }
    diagnostics = []

    _refresh_effective_registrations(
        base_registrations=base,
        closure_files={path: raw},
        closure_hashes={path: "b" * 64},
        selected_key="workflows/other.py::selected",
        selected_registration={"path": "workflows/other.py", "function": "selected"},
        risk_class="R0",
        diagnostics=diagnostics,
    )

    assert [item.code for item in diagnostics] == [
        "non_selected_registration_metadata_changed"
    ]
    assert diagnostics[0].severity == "blocker"
    assert base[key]["name"] == "Second"


def test_r2_multi_workflow_file_defaults_legacy_sibling_bounds() -> None:
    path = "features/meraki/workflows/alerts.py"
    raw = b"""
from bifrost import workflow
@workflow(name="Selected")
async def selected(payload=None): return payload
@workflow(name="Legacy sibling")
async def sibling(payload=None): return payload
"""
    source_hash = hashlib.sha256(raw).hexdigest()
    sibling_key = f"{path}::sibling"
    sibling_id = str(uuid4())
    diagnostics = []

    result = _refresh_effective_registrations(
        base_registrations={
            sibling_key: {
                "path": path,
                "function": "sibling",
                "workflow_id": sibling_id,
                "type": "workflow",
                "name": "Legacy sibling",
                "organization_id": str(uuid4()),
                "is_active": True,
                "source_sha256": "a" * 64,
                "runtime_bounds": {},
            }
        },
        closure_files={path: raw},
        closure_hashes={path: source_hash},
        selected_key=f"{path}::selected",
        selected_registration={
            "path": path,
            "function": "selected",
            "runtime_bounds": R2_POLICY_DEFAULT_BOUNDS,
        },
        risk_class="R2",
        diagnostics=diagnostics,
    )

    assert result[sibling_key]["workflow_id"] == sibling_id
    assert result[sibling_key]["source_sha256"] == source_hash
    assert result[sibling_key]["runtime_bounds"] == R2_POLICY_DEFAULT_BOUNDS
    assert [item.code for item in diagnostics] == [
        "non_selected_registration_policy_bounds"
    ]
    assert diagnostics[0].severity == "warning"


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


def test_first_release_governs_only_closure_and_later_release_accumulates() -> None:
    first = _cumulative_governed_paths(
        (), {"workflows/leaf.py": "a" * 64, "modules/leaf_dep.py": "b" * 64}
    )
    second = _cumulative_governed_paths(tuple(first), {"workflows/second.py": "c" * 64})

    assert first == ["modules/leaf_dep.py", "workflows/leaf.py"]
    assert second == [
        "modules/leaf_dep.py",
        "workflows/leaf.py",
        "workflows/second.py",
    ]


def test_hybrid_base_keeps_current_legacy_bytes_and_overlays_live_governed() -> None:
    repo = {
        "modules/governed.py": b"stale legacy copy\n",
        "modules/legacy.py": b"new legacy revision\n",
    }
    immutable = {
        "modules/governed.py": b"immutable live\n",
        "modules/legacy.py": b"old snapshot only\n",
    }

    hybrid = overlay_governed_base(repo, immutable, ("modules/governed.py",))

    assert hybrid == {
        "modules/governed.py": b"immutable live\n",
        "modules/legacy.py": b"new legacy revision\n",
    }


@pytest.mark.asyncio
async def test_hybrid_snapshot_fails_cas_when_legacy_generation_changes() -> None:
    class Repo:
        async def list(self):
            return ["modules/legacy.py"]

        async def read_many(self, paths, **_kwargs):
            return {path: b"legacy bytes\n" for path in paths}

    generations = iter(("generation-1", "generation-2"))

    async def generation() -> str:
        return next(generations)

    with pytest.raises(WorkspacePromotionInvalid, match="source changed"):
        await read_generation_stable_executable_snapshot(Repo(), generation)


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
