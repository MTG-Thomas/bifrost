"""Focused safety policy tests for rapid Workspace promotion previews."""

from __future__ import annotations

from uuid import uuid4

from src.services.workspace_promotions import (
    WorkspacePromotionPreviewService,
    _canonical_candidate,
    _entry_metadata,
    _source_zip,
    _static_effects,
)


class _MemoryRepo:
    def __init__(self, files: dict[str, bytes]):
        self.files = files

    async def list(self) -> list[str]:
        return list(self.files)

    async def read(self, path: str) -> bytes:
        return self.files[path]


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
    service = WorkspacePromotionPreviewService(
        db=None,  # type: ignore[arg-type] - this pure policy method does not use DB
        organization_id=uuid4(),
        repo_storage=_MemoryRepo(live),  # type: ignore[arg-type]
    )

    diagnostics = await service._reverse_dependency_diagnostics(candidate)

    assert len(diagnostics) == 1
    assert diagnostics[0].code == "shared_dependency_outside_candidate"
    assert diagnostics[0].path == "features/autotask/_ticket_lifecycle.py"
    assert "features/autotask/ticket_webhook.py" in diagnostics[0].message
