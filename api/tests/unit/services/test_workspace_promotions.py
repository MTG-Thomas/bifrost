"""Focused safety policy tests for rapid Workspace promotion previews."""

from __future__ import annotations

from uuid import uuid4

from bifrost.workspace_release import (
    workspace_registration_manifest_id,
    workspace_release_id,
)
from src.models.contracts.workspace_promotions import PromotionEntry
from src.services.workspace_promotions import (
    _allocated_registration_id,
    _canonical_candidate,
    _canonical_impact_diagnostics,
    _closure_id,
    _content_id,
    _entry_metadata,
    _is_executable_python_path,
    _manifest_id,
    _repo_v1_release_id,
    _source_zip,
    _static_effects,
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

    first = _allocated_registration_id(
        organization_id, "workflows/demo.py", "demo"
    )

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
