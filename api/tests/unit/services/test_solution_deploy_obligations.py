import hashlib
import io
import json
import zipfile
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.models.contracts.workspace_promotions import (
    SolutionDeployObligationDeclare,
    SolutionSourceFile,
    WorkspaceSourceReleaseDeclareRequest,
)
from src.models.orm.workspace_promotions import SolutionDeployObligation
from src.services.solution_deploy_obligations import (
    SolutionDeployObligationService,
    reconcile_solution_deploy_obligation,
    solution_deploy_obligation_response,
    solution_source_content_id,
    verify_solution_artifact,
)
from src.services.workspace_source_releases import (
    WorkspaceSourceReleaseConflict,
    WorkspaceSourceReleaseService,
    source_release_declaration_digest,
)


def _zip(entries: list[tuple[str, bytes]], *, reverse: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        iterable = reversed(entries) if reverse else entries
        for path, content in iterable:
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return buffer.getvalue()


def _record(entries: list[tuple[str, bytes]]) -> SolutionDeployObligation:
    repo_subpath = "solutions/example"
    files = [
        {
            "path": f"{repo_subpath}/{path}",
            "mode": "100644",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }
        for path, content in entries
    ]
    return SolutionDeployObligation(
        id=uuid4(),
        source_release_id=uuid4(),
        organization_id=uuid4(),
        source_commit_sha="a" * 40,
        source_tree_sha="b" * 40,
        base_commit_sha="c" * 40,
        solution_slug="example",
        repo_subpath=repo_subpath,
        source_subtree_sha="d" * 40,
        source_content_id=solution_source_content_id(
            solution_slug="example",
            repo_subpath=repo_subpath,
            source_files=files,
        ),
        source_files=files,
        changed_paths={files[-1]["path"]: files[-1]["sha256"]},
        kind="solution_deploy_required",
        disposition="pending",
        declared_disposition="solution_deploy_required",
        due_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def test_artifact_verification_binds_raw_candidate_and_canonical_source_content() -> (
    None
):
    entries = [
        ("bifrost.solution.yaml", b"slug: example\nname: Example\n"),
        ("functions/main.py", b"value = 1\n"),
    ]
    record = _record(entries)
    artifact = _zip(entries)
    candidate_id = f"sha256:{hashlib.sha256(artifact).hexdigest()}"

    valid, reason, evidence = verify_solution_artifact(
        record,
        candidate_id=candidate_id,
        artifact=artifact,
    )

    assert valid is True
    assert reason is None
    assert evidence["candidate_id"] == candidate_id
    assert evidence["candidate_content_id"].startswith("sha256:")
    assert evidence["source_content_id"] == record.source_content_id


def test_zip_order_changes_raw_candidate_but_not_reviewed_content_identity() -> None:
    entries = [
        ("bifrost.solution.yaml", b"slug: example\nname: Example\n"),
        ("functions/main.py", b"value = 1\n"),
    ]
    record = _record(entries)
    first = _zip(entries)
    reordered = _zip(entries, reverse=True)
    assert hashlib.sha256(first).digest() != hashlib.sha256(reordered).digest()

    candidate_id = f"sha256:{hashlib.sha256(reordered).hexdigest()}"
    valid, _, evidence = verify_solution_artifact(
        record,
        candidate_id=candidate_id,
        artifact=reordered,
    )

    assert valid is True
    assert evidence["source_content_id"] == record.source_content_id
    first_valid, _, first_evidence = verify_solution_artifact(
        record,
        candidate_id=f"sha256:{hashlib.sha256(first).hexdigest()}",
        artifact=first,
    )
    assert first_valid is True
    assert first_evidence["candidate_content_id"] == evidence["candidate_content_id"]


def test_unreviewed_non_runtime_artifact_file_cannot_close_reviewed_source() -> None:
    entries = [
        ("bifrost.solution.yaml", b"slug: example\nname: Example\n"),
        ("functions/main.py", b"value = 1\n"),
    ]
    record = _record(entries)
    artifact = _zip([*entries, ("unreviewed.txt", b"bad")])

    valid, reason, evidence = verify_solution_artifact(
        record,
        candidate_id=f"sha256:{hashlib.sha256(artifact).hexdigest()}",
        artifact=artifact,
    )

    assert valid is False
    assert reason == "stored source artifact file manifest differs from reviewed Git"
    assert len(evidence["observed_files"]) == 3


def test_unreviewed_vendored_python_cannot_close_obligation() -> None:
    entries = [
        ("bifrost.solution.yaml", b"slug: example\nname: Example\n"),
        ("functions/main.py", b"value = 1\n"),
    ]
    record = _record(entries)
    artifact = _zip([*entries, ("shared/vendor.py", b"value = 2\n")])

    valid, reason, evidence = verify_solution_artifact(
        record,
        candidate_id=f"sha256:{hashlib.sha256(artifact).hexdigest()}",
        artifact=artifact,
    )

    assert valid is False
    assert reason == "stored source artifact file manifest differs from reviewed Git"
    assert evidence["unexpected_paths"] == ["solutions/example/shared/vendor.py"]


def test_solution_declaration_serialization_omits_nested_nulls() -> None:
    request = SolutionDeployObligationDeclare(
        solution_slug="example",
        repo_subpath="solutions/example",
        changed_paths={"solutions/example/deleted.py": None},
        disposition="attention_required",
        reason="Solution directory was deleted",
    )

    assert request.model_dump(mode="json") == {
        "solution_slug": "example",
        "repo_subpath": "solutions/example",
        "source_files": [],
        "changed_paths": {"solutions/example/deleted.py": None},
        "disposition": "attention_required",
        "reason": "Solution directory was deleted",
    }


def test_deploy_required_contract_recomputes_exact_source_content_id() -> None:
    source_file = SolutionSourceFile(
        path="solutions/example/bifrost.solution.yaml",
        mode="100644",
        sha256="a" * 64,
        size=10,
    )
    content_id = solution_source_content_id(
        solution_slug="example",
        repo_subpath="solutions/example",
        source_files=[source_file.model_dump(mode="json")],
    )
    request = SolutionDeployObligationDeclare(
        solution_slug="example",
        repo_subpath="solutions/example",
        base_commit_sha="b" * 40,
        source_subtree_sha="c" * 40,
        source_content_id=content_id,
        source_files=[source_file],
        changed_paths={source_file.path: source_file.sha256},
        disposition="solution_deploy_required",
    )

    assert request.source_content_id == content_id


def test_deploy_required_contract_rejects_missing_exact_source_evidence() -> None:
    with pytest.raises(ValidationError, match="exact source evidence"):
        SolutionDeployObligationDeclare(
            solution_slug="example",
            repo_subpath="solutions/example",
            changed_paths={"solutions/example/file.py": "a" * 64},
            disposition="solution_deploy_required",
        )


def test_solution_contract_rejects_changed_path_manifest_mismatch() -> None:
    source_file = SolutionSourceFile(
        path="solutions/example/file.py",
        mode="100644",
        sha256="a" * 64,
        size=1,
    )
    with pytest.raises(ValidationError, match="contradict"):
        SolutionDeployObligationDeclare(
            solution_slug="example",
            repo_subpath="solutions/example",
            source_subtree_sha="c" * 40,
            source_content_id="sha256:" + "d" * 64,
            source_files=[source_file],
            changed_paths={source_file.path: "b" * 64},
            disposition="solution_deploy_required",
        )


def test_solution_contract_reports_invalid_changed_digest_before_manifest_mismatch() -> (
    None
):
    source_file = SolutionSourceFile(
        path="solutions/example/file.py",
        mode="100644",
        sha256="a" * 64,
        size=1,
    )
    with pytest.raises(ValidationError, match="lowercase SHA-256"):
        SolutionDeployObligationDeclare(
            solution_slug="example",
            repo_subpath="solutions/example",
            source_subtree_sha="c" * 40,
            source_content_id="sha256:" + "d" * 64,
            source_files=[source_file],
            changed_paths={source_file.path: "NOT-A-DIGEST"},
            disposition="solution_deploy_required",
        )


def test_old_declaration_digest_is_unchanged_when_solution_field_is_absent() -> None:
    request = WorkspaceSourceReleaseDeclareRequest(
        source_commit_sha="a" * 40,
        source_tree_sha="b" * 40,
        paths={},
        disposition="non_production",
        reason="No production-managed paths changed.",
    )
    old_shape = {
        "source_commit_sha": "a" * 40,
        "source_tree_sha": "b" * 40,
        "paths": {},
        "disposition": "non_production",
        "reason": "No production-managed paths changed.",
    }
    encoded = json.dumps(
        old_shape,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()

    assert (
        source_release_declaration_digest(request)
        == hashlib.sha256(encoded).hexdigest()
    )


def test_solution_obligation_migration_extends_current_head() -> None:
    migration = (
        Path(__file__).resolve().parents[3]
        / "alembic"
        / "versions"
        / "20260826_solution_deploy_obligations.py"
    ).read_text()

    assert 'down_revision: str | None = "20260825_delivery_attempt"' in migration


def test_attention_only_obligation_remains_readable_without_source_identity() -> None:
    record = SolutionDeployObligation(
        id=uuid4(),
        source_release_id=uuid4(),
        organization_id=uuid4(),
        source_commit_sha="a" * 40,
        source_tree_sha="b" * 40,
        base_commit_sha="c" * 40,
        solution_slug="example",
        repo_subpath="solutions/example",
        source_subtree_sha=None,
        source_content_id=None,
        source_files=[],
        changed_paths={"solutions/example/deleted.py": None},
        kind="solution_deploy_required",
        disposition="attention_required",
        declared_disposition="attention_required",
        reason="Solution directory was deleted",
        due_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    response = solution_deploy_obligation_response(record)

    assert response.source_content_id is None
    assert response.requires_attention is True


@pytest.mark.asyncio
async def test_list_counts_overdue_rows_outside_page() -> None:
    record = _record([("bifrost.solution.yaml", b"slug: example\nname: Example\n")])
    database = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [record])),
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [("pending", 7)])),
        scalar=AsyncMock(side_effect=[2, 3]),
    )

    response = await SolutionDeployObligationService(
        database, record.organization_id
    ).list(limit=1)

    assert response.total == 7
    assert response.pending == 7
    assert response.attention_required == 2
    assert response.overdue == 3


@pytest.mark.asyncio
async def test_exact_artifact_and_runtime_readback_close_obligation(
    monkeypatch,
) -> None:
    entries = [
        ("bifrost.solution.yaml", b"slug: example\nname: Example\n"),
        ("functions/main.py", b"value = 1\n"),
    ]
    record = _record(entries)
    artifact = _zip(entries)
    candidate_id = f"sha256:{hashlib.sha256(artifact).hexdigest()}"
    database = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [record])),
        flush=AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.solution_deploy_obligations._runtime_and_registration_readback",
        AsyncMock(return_value=(True, None, {"runtime_files": {}})),
    )

    result = await reconcile_solution_deploy_obligation(
        database,
        solution_id=uuid4(),
        solution_slug="example",
        accountability_organization_id=record.organization_id,
        deploy_job_id=uuid4(),
        candidate_id=candidate_id,
        artifact=artifact,
    )

    assert result["state"] == "released"
    assert record.disposition == "released"
    assert record.completion_evidence["artifact"]["candidate_id"] == candidate_id
    assert record.completion_evidence["evidence_id"] == result["evidence_id"]
    database.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_mismatch_stays_attention_required(monkeypatch) -> None:
    entries = [
        ("bifrost.solution.yaml", b"slug: example\nname: Example\n"),
        ("functions/main.py", b"value = 1\n"),
    ]
    record = _record(entries)
    artifact = _zip(entries)
    database = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [record])),
        flush=AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.solution_deploy_obligations._runtime_and_registration_readback",
        AsyncMock(
            return_value=(
                False,
                "workflow registration readback differs",
                {"expected": ["workflow-1"], "observed": []},
            )
        ),
    )

    result = await reconcile_solution_deploy_obligation(
        database,
        solution_id=uuid4(),
        solution_slug="example",
        accountability_organization_id=record.organization_id,
        deploy_job_id=uuid4(),
        candidate_id=f"sha256:{hashlib.sha256(artifact).hexdigest()}",
        artifact=artifact,
    )

    assert result["state"] == "attention_required"
    assert record.disposition == "attention_required"
    assert record.resolved_at is None
    assert record.completion_evidence["mismatch"]["observed"] == []


@pytest.mark.asyncio
async def test_readback_exception_becomes_attention_evidence(monkeypatch) -> None:
    entries = [("bifrost.solution.yaml", b"slug: example\nname: Example\n")]
    record = _record(entries)
    artifact = _zip(entries)
    database = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [record])),
        flush=AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.solution_deploy_obligations._runtime_and_registration_readback",
        AsyncMock(side_effect=RuntimeError("storage unavailable")),
    )

    result = await reconcile_solution_deploy_obligation(
        database,
        solution_id=uuid4(),
        solution_slug="example",
        accountability_organization_id=record.organization_id,
        deploy_job_id=uuid4(),
        candidate_id=f"sha256:{hashlib.sha256(artifact).hexdigest()}",
        artifact=artifact,
    )

    assert result["state"] == "attention_required"
    assert record.reason == "Solution deployment readback failed: RuntimeError"
    assert record.completion_evidence == {"mismatch": {"error_type": "RuntimeError"}}


@pytest.mark.asyncio
async def test_reconcile_skips_newer_artifact_mismatch_and_releases_exact_record(
    monkeypatch,
) -> None:
    old_entries = [("bifrost.solution.yaml", b"slug: example\nname: Example\n")]
    new_entries = [*old_entries, ("functions/new.py", b"value = 2\n")]
    older = _record(old_entries)
    newer = _record(new_entries)
    artifact = _zip(old_entries)
    database = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [newer, older])),
        flush=AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.solution_deploy_obligations._runtime_and_registration_readback",
        AsyncMock(return_value=(True, None, {"runtime_files": {}})),
    )

    result = await reconcile_solution_deploy_obligation(
        database,
        solution_id=uuid4(),
        solution_slug="example",
        accountability_organization_id=older.organization_id,
        deploy_job_id=uuid4(),
        candidate_id=f"sha256:{hashlib.sha256(artifact).hexdigest()}",
        artifact=artifact,
    )

    assert result["state"] == "released"
    assert older.disposition == "released"
    assert newer.disposition == "pending"


@pytest.mark.asyncio
async def test_all_artifact_mismatches_mark_newest_attention() -> None:
    first = _record([("bifrost.solution.yaml", b"slug: example\nname: First\n")])
    second = _record([("bifrost.solution.yaml", b"slug: example\nname: Second\n")])
    artifact = _zip([("bifrost.solution.yaml", b"slug: example\nname: Other\n")])
    database = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [first, second])),
        flush=AsyncMock(),
    )

    result = await reconcile_solution_deploy_obligation(
        database,
        solution_id=uuid4(),
        solution_slug="example",
        accountability_organization_id=first.organization_id,
        deploy_job_id=uuid4(),
        candidate_id=f"sha256:{hashlib.sha256(artifact).hexdigest()}",
        artifact=artifact,
    )

    assert result["state"] == "attention_required"
    assert first.disposition == "attention_required"
    assert first.source_artifact_sha256 == hashlib.sha256(artifact).hexdigest()
    assert second.disposition == "pending"


@pytest.mark.asyncio
async def test_overdue_attention_can_close_after_exact_deploy(monkeypatch) -> None:
    entries = [("bifrost.solution.yaml", b"slug: example\nname: Example\n")]
    record = _record(entries)
    record.disposition = "attention_required"
    record.reason = "reviewed Solution source has not reached verified production"
    artifact = _zip(entries)
    database = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [record])),
        flush=AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.solution_deploy_obligations._runtime_and_registration_readback",
        AsyncMock(return_value=(True, None, {"runtime_files": {}})),
    )

    result = await reconcile_solution_deploy_obligation(
        database,
        solution_id=uuid4(),
        solution_slug="example",
        accountability_organization_id=record.organization_id,
        deploy_job_id=uuid4(),
        candidate_id=f"sha256:{hashlib.sha256(artifact).hexdigest()}",
        artifact=artifact,
    )

    assert result["state"] == "released"
    assert record.reason is None


@pytest.mark.asyncio
async def test_source_declaration_returns_exact_solution_evidence() -> None:
    organization_id = uuid4()
    source_file = SolutionSourceFile(
        path="solutions/example/bifrost.solution.yaml",
        mode="100644",
        sha256="d" * 64,
        size=42,
    )
    obligation = SolutionDeployObligationDeclare(
        solution_slug="example",
        repo_subpath="solutions/example",
        base_commit_sha="c" * 40,
        source_subtree_sha="d" * 40,
        source_content_id=solution_source_content_id(
            solution_slug="example",
            repo_subpath="solutions/example",
            source_files=[source_file.model_dump(mode="json")],
        ),
        source_files=[source_file],
        changed_paths={source_file.path: source_file.sha256},
        disposition="solution_deploy_required",
    )
    request = WorkspaceSourceReleaseDeclareRequest(
        source_commit_sha="a" * 40,
        source_tree_sha="b" * 40,
        disposition="non_production",
        reason="Only governed Solution source changed.",
        solution_deploy_obligations=[obligation],
    )

    class Database:
        def __init__(self):
            self.records = []

        async def scalar(self, _statement):
            return None

        async def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [])

        def add(self, record):
            self.records.append(record)
            if record.created_at is None:
                record.created_at = datetime.now(timezone.utc)
                record.updated_at = record.created_at

        async def commit(self):
            pass

        async def refresh(self, record, attribute_names=None):
            if attribute_names == ["solution_deploy_obligations"]:
                record.solution_deploy_obligations = [
                    item
                    for item in self.records
                    if isinstance(item, SolutionDeployObligation)
                ]

    response = await WorkspaceSourceReleaseService(Database(), organization_id).declare(
        request, created_by=uuid4()
    )

    assert [
        item.model_dump(mode="json") for item in response.solution_deploy_obligations
    ] == [obligation.model_dump(mode="json")]


@pytest.mark.asyncio
async def test_source_declaration_replay_cannot_drop_solution_evidence() -> None:
    source = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        source_commit_sha="a" * 40,
        source_tree_sha="b" * 40,
        paths={},
        declared_disposition="non_production",
        reason="Only governed Solution source changed.",
        solution_deploy_obligations=[
            _record([("bifrost.solution.yaml", b"slug: example\nname: Example\n")])
        ],
    )
    database = SimpleNamespace(scalar=AsyncMock(return_value=source))
    request = WorkspaceSourceReleaseDeclareRequest(
        source_commit_sha="a" * 40,
        source_tree_sha="b" * 40,
        disposition="non_production",
        reason="Only governed Solution source changed.",
    )

    with pytest.raises(WorkspaceSourceReleaseConflict):
        await WorkspaceSourceReleaseService(database, source.organization_id).declare(
            request, created_by=uuid4()
        )
