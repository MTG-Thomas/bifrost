"""Durable accountability for reviewed Solution source deployments."""

from __future__ import annotations

import hashlib
import io
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost.workspace_release import canonical_digest
from src.models.contracts.workspace_promotions import (
    SolutionDeployObligationDeclare,
    SolutionDeployObligationListResponse,
    SolutionDeployObligationResponse,
)
from src.models.orm.workspace_promotions import SolutionDeployObligation

DEFAULT_SOLUTION_DEPLOY_DUE_AFTER = timedelta(minutes=30)


class SolutionDeployObligationConflict(ValueError):
    """One source commit and Solution slug were redeclared with other evidence."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def solution_source_content_id(
    *,
    solution_slug: str,
    repo_subpath: str,
    source_files: list[dict[str, object]],
) -> str:
    return canonical_digest(
        {
            "schema_version": "bifrost.solution-source/v1",
            "solution_slug": solution_slug,
            "repo_subpath": repo_subpath,
            "files": source_files,
        }
    )


def _request_files(request: SolutionDeployObligationDeclare) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in request.source_files]


def _assert_valid_content_id(request: SolutionDeployObligationDeclare) -> None:
    if request.disposition == "attention_required":
        return
    actual = solution_source_content_id(
        solution_slug=request.solution_slug,
        repo_subpath=request.repo_subpath,
        source_files=_request_files(request),
    )
    if actual != request.source_content_id:
        raise SolutionDeployObligationConflict(
            "Solution source_content_id does not match its exact file manifest"
        )


def _assert_compatible(
    record: SolutionDeployObligation,
    request: SolutionDeployObligationDeclare,
    *,
    source_tree_sha: str,
) -> None:
    expected = {
        "source_tree_sha": source_tree_sha,
        "base_commit_sha": request.base_commit_sha,
        "repo_subpath": request.repo_subpath,
        "source_subtree_sha": request.source_subtree_sha,
        "source_content_id": request.source_content_id,
        "base_source_content_id": request.base_source_content_id,
        "declared_version": request.declared_version,
        "source_files": _request_files(request),
        "changed_paths": dict(sorted(request.changed_paths.items())),
        "declared_disposition": request.disposition,
        "reason": request.reason,
    }
    observed = {key: getattr(record, key) for key in expected}
    if observed != expected:
        raise SolutionDeployObligationConflict(
            "source commit already has different Solution deploy evidence"
        )


async def declare_solution_deploy_obligations(
    db: AsyncSession,
    *,
    source_release_id: UUID,
    organization_id: UUID,
    source_commit_sha: str,
    source_tree_sha: str,
    requests: list[SolutionDeployObligationDeclare],
) -> None:
    now = _utc_now()
    for request in requests:
        _assert_valid_content_id(request)
        existing = await db.scalar(
            select(SolutionDeployObligation).where(
                SolutionDeployObligation.organization_id == organization_id,
                SolutionDeployObligation.source_commit_sha == source_commit_sha,
                SolutionDeployObligation.solution_slug == request.solution_slug,
            )
        )
        if existing is not None:
            _assert_compatible(existing, request, source_tree_sha=source_tree_sha)
            continue

        if request.base_source_content_id:
            prior = list(
                (
                    await db.scalars(
                        select(SolutionDeployObligation).where(
                            SolutionDeployObligation.organization_id == organization_id,
                            SolutionDeployObligation.solution_slug
                            == request.solution_slug,
                            SolutionDeployObligation.source_content_id
                            == request.base_source_content_id,
                            SolutionDeployObligation.disposition.in_(
                                ("pending", "attention_required")
                            ),
                        )
                    )
                ).all()
            )
            for record in prior:
                record.disposition = "superseded"
                record.reason = (
                    f"Superseded by reviewed source commit {source_commit_sha}"
                )
                record.resolved_at = now
        elif request.disposition == "solution_deploy_required":
            deleted_or_invalid = list(
                (
                    await db.scalars(
                        select(SolutionDeployObligation).where(
                            SolutionDeployObligation.organization_id
                            == organization_id,
                            SolutionDeployObligation.solution_slug
                            == request.solution_slug,
                            SolutionDeployObligation.source_content_id.is_(None),
                            SolutionDeployObligation.disposition
                            == "attention_required",
                        )
                    )
                ).all()
            )
            for prior_record in deleted_or_invalid:
                prior_record.disposition = "superseded"
                prior_record.reason = (
                    f"Superseded by valid reviewed source commit {source_commit_sha}"
                )
                prior_record.resolved_at = now

        record = SolutionDeployObligation(
            source_release_id=source_release_id,
            organization_id=organization_id,
            source_commit_sha=source_commit_sha,
            source_tree_sha=source_tree_sha,
            base_commit_sha=request.base_commit_sha,
            solution_slug=request.solution_slug,
            repo_subpath=request.repo_subpath,
            source_subtree_sha=request.source_subtree_sha,
            source_content_id=request.source_content_id,
            base_source_content_id=request.base_source_content_id,
            declared_version=request.declared_version,
            source_files=_request_files(request),
            changed_paths=dict(sorted(request.changed_paths.items())),
            kind="solution_deploy_required",
            disposition=(
                "pending"
                if request.disposition == "solution_deploy_required"
                else "attention_required"
            ),
            declared_disposition=request.disposition,
            reason=request.reason,
            due_at=(
                now + DEFAULT_SOLUTION_DEPLOY_DUE_AFTER
                if request.disposition == "solution_deploy_required"
                else now
            ),
        )
        db.add(record)


def solution_deploy_obligation_response(
    record: SolutionDeployObligation,
    *,
    now: datetime | None = None,
) -> SolutionDeployObligationResponse:
    now = now or _utc_now()
    overdue = bool(
        record.disposition in {"pending", "attention_required"}
        and record.due_at is not None
        and record.due_at <= now
    )
    return SolutionDeployObligationResponse(
        id=record.id,
        source_release_id=record.source_release_id,
        organization_id=record.organization_id,
        source_commit_sha=record.source_commit_sha,
        source_tree_sha=record.source_tree_sha,
        base_commit_sha=record.base_commit_sha,
        kind=record.kind,
        solution_slug=record.solution_slug,
        repo_subpath=record.repo_subpath,
        source_subtree_sha=record.source_subtree_sha,
        source_content_id=record.source_content_id,
        base_source_content_id=record.base_source_content_id,
        declared_version=record.declared_version,
        source_files=list(record.source_files or []),
        changed_paths=dict(record.changed_paths or {}),
        disposition=record.disposition,
        reason=record.reason,
        solution_id=record.solution_id,
        deploy_job_id=record.deploy_job_id,
        candidate_id=record.candidate_id,
        source_artifact_sha256=record.source_artifact_sha256,
        completion_evidence=record.completion_evidence,
        due_at=record.due_at,
        overdue=overdue,
        requires_attention=record.disposition == "attention_required" or overdue,
        resolved_at=record.resolved_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def solution_deploy_obligation_declaration(
    record: SolutionDeployObligation,
) -> SolutionDeployObligationDeclare:
    return SolutionDeployObligationDeclare(
        solution_slug=record.solution_slug,
        repo_subpath=record.repo_subpath,
        base_commit_sha=record.base_commit_sha,
        source_subtree_sha=record.source_subtree_sha,
        source_content_id=record.source_content_id,
        base_source_content_id=record.base_source_content_id,
        declared_version=record.declared_version,
        source_files=list(record.source_files or []),
        changed_paths=dict(record.changed_paths or {}),
        disposition=record.declared_disposition,
        reason=record.reason
        if record.declared_disposition == "attention_required"
        else None,
    )


class SolutionDeployObligationService:
    def __init__(self, db: AsyncSession, organization_id: UUID):
        self.db = db
        self.organization_id = organization_id

    async def list(self, *, limit: int = 100) -> SolutionDeployObligationListResponse:
        records = list(
            (
                await self.db.scalars(
                    select(SolutionDeployObligation)
                    .where(
                        SolutionDeployObligation.organization_id == self.organization_id
                    )
                    .order_by(SolutionDeployObligation.created_at.desc())
                    .limit(limit)
                )
            ).all()
        )
        now = _utc_now()
        responses = [
            solution_deploy_obligation_response(row, now=now) for row in records
        ]
        counts = {
            disposition: int(count)
            for disposition, count in (
                await self.db.execute(
                    select(
                        SolutionDeployObligation.disposition,
                        func.count(SolutionDeployObligation.id),
                    )
                    .where(
                        SolutionDeployObligation.organization_id == self.organization_id
                    )
                    .group_by(SolutionDeployObligation.disposition)
                )
            ).all()
        }
        overdue = sum(1 for item in responses if item.overdue)
        return SolutionDeployObligationListResponse(
            records=responses,
            total=sum(counts.values()),
            pending=counts.get("pending", 0),
            attention_required=counts.get("attention_required", 0)
            + sum(
                1
                for item in responses
                if item.disposition == "pending" and item.overdue
            ),
            overdue=overdue,
        )

    async def get(self, record_id: UUID) -> SolutionDeployObligationResponse:
        record = await self.db.scalar(
            select(SolutionDeployObligation).where(
                SolutionDeployObligation.id == record_id,
                SolutionDeployObligation.organization_id == self.organization_id,
            )
        )
        if record is None:
            raise KeyError(record_id)
        return solution_deploy_obligation_response(record)


async def sweep_overdue_solution_deploys(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> list[str]:
    now = now or _utc_now()
    records = list(
        (
            await db.scalars(
                select(SolutionDeployObligation)
                .where(
                    SolutionDeployObligation.disposition == "pending",
                    SolutionDeployObligation.due_at.is_not(None),
                    SolutionDeployObligation.due_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for record in records:
        record.disposition = "attention_required"
        record.reason = "reviewed Solution source has not reached verified production"
    await db.flush()
    return [str(record.id) for record in records]


def _artifact_file_hashes(data: bytes, repo_subpath: str) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            relative = info.filename.strip("/")
            content = archive.read(info)
            files.append(
                {
                    "path": f"{repo_subpath}/{relative}",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                }
            )
    return sorted(files, key=lambda item: str(item["path"]))


def verify_solution_artifact(
    record: SolutionDeployObligation,
    *,
    candidate_id: str,
    artifact: bytes,
) -> tuple[bool, str | None, dict[str, Any]]:
    raw_sha256 = hashlib.sha256(artifact).hexdigest()
    if candidate_id != f"sha256:{raw_sha256}":
        return False, "candidate digest does not match the stored source artifact", {}

    expected = list(record.source_files or [])
    expected_without_mode = [
        {"path": item["path"], "sha256": item["sha256"], "size": item["size"]}
        for item in expected
    ]
    observed = _artifact_file_hashes(artifact, record.repo_subpath)
    candidate_content_id = canonical_digest(
        {
            "schema_version": "bifrost.solution-candidate/v1",
            "files": observed,
        }
    )
    expected_by_path = {item["path"]: item for item in expected_without_mode}
    observed_by_path = {item["path"]: item for item in observed}
    missing = sorted(set(expected_by_path) - set(observed_by_path))
    modified = sorted(
        path
        for path in set(expected_by_path) & set(observed_by_path)
        if expected_by_path[path] != observed_by_path[path]
    )
    derived_manifest = f"{record.repo_subpath}/.bifrost/apps.yaml"
    invalid_modified = [path for path in modified if path != derived_manifest]
    extras = sorted(set(observed_by_path) - set(expected_by_path))
    invalid_extras = [path for path in extras if not path.endswith(".py")]
    if missing or invalid_modified or invalid_extras:
        return (
            False,
            "stored source artifact file manifest differs from reviewed Git",
            {
                "expected_files": expected_without_mode,
                "observed_files": observed,
                "missing_paths": missing,
                "modified_paths": invalid_modified,
                "unexpected_paths": invalid_extras,
            },
        )
    actual_content_id = solution_source_content_id(
        solution_slug=record.solution_slug,
        repo_subpath=record.repo_subpath,
        source_files=expected,
    )
    if actual_content_id != record.source_content_id:
        return (
            False,
            "stored source artifact content identity differs from reviewed Git",
            {},
        )
    return (
        True,
        None,
        {
            "candidate_id": candidate_id,
            "candidate_content_id": candidate_content_id,
            "source_artifact_sha256": raw_sha256,
            "source_content_id": actual_content_id,
            "source_files": observed,
            "derived_paths": sorted([*extras, *modified]),
        },
    )


async def _runtime_and_registration_readback(
    db: AsyncSession,
    *,
    solution_id: UUID,
    artifact: bytes,
) -> tuple[bool, str | None, dict[str, Any]]:
    from src.models.orm.agents import Agent
    from src.models.orm.applications import Application
    from src.models.orm.custom_claims import CustomClaim
    from src.models.orm.events import EventSource
    from src.models.orm.file_metadata import FilePolicy
    from src.models.orm.forms import Form
    from src.models.orm.solution_config_schema import SolutionConfigSchema
    from src.models.orm.tables import Table
    from src.models.orm.workflows import Workflow
    from src.services.solutions.deploy import solution_entity_id
    from src.services.solutions.storage import SolutionStorage
    from src.services.solutions.zip_install import _safe_extract, preview_zip

    preview = preview_zip(artifact)
    entity_specs = {
        "workflows": (Workflow, preview.workflows),
        "tables": (Table, preview.tables),
        "apps": (Application, preview.apps),
        "forms": (Form, preview.forms),
        "agents": (Agent, preview.agents),
        "claims": (CustomClaim, preview.claims),
        "config_schemas": (SolutionConfigSchema, preview.config_schemas),
        "events": (EventSource, preview.events),
        "file_policies": (FilePolicy, preview.file_policies),
    }
    entity_readback: dict[str, list[str]] = {}
    for name, (model, entries) in entity_specs.items():
        expected = sorted(
            str(solution_entity_id(solution_id, UUID(str(entry["id"]))))
            for entry in entries
        )
        actual = sorted(
            str(item)
            for item in (
                await db.scalars(
                    select(model.id).where(model.solution_id == solution_id)
                )
            ).all()
        )
        if actual != expected:
            return (
                False,
                f"{name} readback differs from the deployed manifest",
                {
                    "entity_type": name,
                    "expected_ids": expected,
                    "observed_ids": actual,
                },
            )
        entity_readback[name] = actual

    expected_workflows = sorted(
        (
            str(solution_entity_id(solution_id, UUID(str(entry["id"])))),
            str(entry["path"]),
            str(entry["function_name"]),
            str(entry.get("name") or entry["id"]),
        )
        for entry in preview.workflows
    )
    actual_workflows = sorted(
        (str(row.id), row.path, row.function_name, row.name)
        for row in (
            await db.scalars(
                select(Workflow).where(Workflow.solution_id == solution_id)
            )
        ).all()
    )
    if actual_workflows != expected_workflows:
        return (
            False,
            "workflow registration readback differs from the deployed manifest",
            {
                "expected": expected_workflows,
                "observed": actual_workflows,
            },
        )

    with tempfile.TemporaryDirectory(prefix="bifrost-solution-accountability-") as tmp:
        _safe_extract(artifact, tmp)
        from bifrost.commands.solution import _collect_python_files

        python_files = _collect_python_files(Path(tmp))
    expected_runtime = {
        path: hashlib.sha256(content.encode("utf-8")).hexdigest()
        for path, content in sorted(python_files.items())
    }
    storage = SolutionStorage(solution_id)
    actual_paths = sorted(await storage.list(""))
    if actual_paths != sorted(expected_runtime):
        return (
            False,
            "Solution runtime file set differs from the stored source artifact",
            {
                "expected_paths": sorted(expected_runtime),
                "observed_paths": actual_paths,
            },
        )
    actual_runtime = {
        path: hashlib.sha256(await storage.read(path)).hexdigest()
        for path in actual_paths
    }
    if actual_runtime != expected_runtime:
        return (
            False,
            "Solution runtime file hashes differ from the stored source artifact",
            {
                "expected": expected_runtime,
                "observed": actual_runtime,
            },
        )
    from src.core.module_cache import (
        MODULE_INDEX_KEY,
        MODULE_KEY_PREFIX,
        _scan_keys,
        get_module,
    )
    from src.core.redis_client import get_redis_client

    runtime_prefix = f"_solutions/{solution_id}/"
    expected_cached = sorted(
        f"{runtime_prefix}{path}"
        for path in expected_runtime
        if path.endswith(".py")
    )
    redis_conn = await get_redis_client()._get_redis()
    indexed_raw = await redis_conn.smembers(MODULE_INDEX_KEY)
    indexed = {
        item if isinstance(item, str) else item.decode() for item in indexed_raw
    }
    cached_keys = await _scan_keys(
        redis_conn, f"{MODULE_KEY_PREFIX}{runtime_prefix}*"
    )
    cached_paths = {
        (item if isinstance(item, str) else item.decode()).removeprefix(
            MODULE_KEY_PREFIX
        )
        for item in cached_keys
    }
    observed_cached = sorted(
        path for path in indexed | cached_paths if path.startswith(runtime_prefix)
    )
    if observed_cached != expected_cached:
        return (
            False,
            "Solution runtime cache file set differs from the artifact",
            {
                "expected_cache_paths": expected_cached,
                "observed_cache_paths": observed_cached,
            },
        )
    cache_hashes: dict[str, str] = {}
    for storage_path in expected_cached:
        cached = await get_module(storage_path)
        if cached is None:
            return False, "Solution runtime cache is missing an artifact module", {
                "path": storage_path
            }
        cache_hashes[storage_path.removeprefix(runtime_prefix)] = hashlib.sha256(
            cached["content"].encode("utf-8")
        ).hexdigest()
    if cache_hashes != expected_runtime:
        return False, "Solution runtime cache hashes differ from the artifact", {
            "expected": expected_runtime,
            "observed": cache_hashes,
        }
    return (
        True,
        None,
        {
            "runtime_files": actual_runtime,
            "runtime_cache_files": cache_hashes,
            "entity_ids": entity_readback,
            "workflow_registrations": [list(item) for item in actual_workflows],
        },
    )


async def reconcile_solution_deploy_obligation(
    db: AsyncSession,
    *,
    solution_id: UUID,
    solution_slug: str,
    accountability_organization_id: UUID,
    deploy_job_id: UUID,
    candidate_id: str,
    artifact: bytes,
    verified_at: datetime | None = None,
) -> dict[str, Any]:
    """Close only the exact reviewed obligation proven by artifact and readback."""
    verified_at = verified_at or _utc_now()
    query = select(SolutionDeployObligation).where(
        SolutionDeployObligation.organization_id == accountability_organization_id,
        SolutionDeployObligation.solution_slug == solution_slug,
        SolutionDeployObligation.declared_disposition == "solution_deploy_required",
        SolutionDeployObligation.disposition.in_(("pending", "attention_required")),
    )
    records = list(
        (
            await db.scalars(
                query.order_by(
                    SolutionDeployObligation.created_at.desc()
                ).with_for_update()
            )
        ).all()
    )
    if not records:
        return {"state": "not_tracked"}

    mismatch: tuple[SolutionDeployObligation, str, dict[str, Any]] | None = None
    for record in records:
        valid, reason, artifact_evidence = verify_solution_artifact(
            record,
            candidate_id=candidate_id,
            artifact=artifact,
        )
        if not valid:
            if mismatch is None:
                mismatch = (
                    record,
                    reason or "source artifact mismatch",
                    artifact_evidence,
                )
            continue
        valid, reason, readback = await _runtime_and_registration_readback(
            db,
            solution_id=solution_id,
            artifact=artifact,
        )
        if not valid:
            record.disposition = "attention_required"
            record.reason = reason
            record.solution_id = solution_id
            record.deploy_job_id = deploy_job_id
            record.candidate_id = candidate_id
            record.source_artifact_sha256 = artifact_evidence["source_artifact_sha256"]
            record.completion_evidence = {"mismatch": readback}
            await db.flush()
            return {
                "state": "attention_required",
                "obligation_id": str(record.id),
                "reason": reason,
            }

        evidence: dict[str, Any] = {
            "schema_version": "bifrost.solution-deploy-completion/v1",
            "source_commit_sha": record.source_commit_sha,
            "source_tree_sha": record.source_tree_sha,
            "source_subtree_sha": record.source_subtree_sha,
            "solution_slug": solution_slug,
            "solution_id": str(solution_id),
            "deploy_job_id": str(deploy_job_id),
            "artifact": artifact_evidence,
            "readback": readback,
            "verified_at": verified_at.isoformat(),
        }
        evidence["evidence_id"] = canonical_digest(evidence)
        record.disposition = "released"
        record.reason = None
        record.solution_id = solution_id
        record.deploy_job_id = deploy_job_id
        record.candidate_id = candidate_id
        record.source_artifact_sha256 = artifact_evidence["source_artifact_sha256"]
        record.completion_evidence = evidence
        record.resolved_at = verified_at
        await db.flush()
        return {
            "state": "released",
            "obligation_id": str(record.id),
            "evidence_id": evidence["evidence_id"],
        }

    if mismatch is not None:
        record, reason, detail = mismatch
        record.disposition = "attention_required"
        record.reason = reason
        record.solution_id = solution_id
        record.deploy_job_id = deploy_job_id
        record.candidate_id = candidate_id
        record.source_artifact_sha256 = hashlib.sha256(artifact).hexdigest()
        record.completion_evidence = {"mismatch": detail}
        await db.flush()
        return {
            "state": "attention_required",
            "obligation_id": str(record.id),
            "reason": reason,
        }
    return {"state": "not_tracked"}


__all__ = [
    "SolutionDeployObligationConflict",
    "SolutionDeployObligationService",
    "declare_solution_deploy_obligations",
    "solution_deploy_obligation_response",
    "solution_deploy_obligation_declaration",
    "solution_source_content_id",
    "sweep_overdue_solution_deploys",
    "reconcile_solution_deploy_obligation",
    "verify_solution_artifact",
]
