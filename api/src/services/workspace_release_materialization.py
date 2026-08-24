"""Prepare one reviewed Workspace artifact into immutable executable storage."""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost.promotion import (
    MAX_CLOSURE_BYTES,
    MAX_CLOSURE_FILES,
    MAX_SNAPSHOT_FILES,
    sha256_bytes,
)
from bifrost.workspace_release import (
    canonical_digest,
    repo_v1_release_id,
    workspace_closure_id,
    workspace_cohort_closure_id,
    workspace_content_id,
    workspace_manifest_id,
    workspace_release_id,
)
from bifrost.workspace_release_authorization import (
    activation_challenge,
    computed_effects_id,
)
from src.models.orm.workspace_promotions import (
    WorkspacePromotionArtifact,
    WorkspacePromotionRelease,
)
from src.services.repo_storage import RepoStorage
from src.services.workspace_promotion_storage import WorkspacePromotionArtifactStorage
from src.services.workspace_promotions import (
    PROMOTION_ARTIFACT_SCHEMA,
    PROMOTION_BUNDLE_SCHEMA_V2,
    _canonical_candidate,
    _is_executable_python_path,
    _promotion_risk_class,
    overlay_governed_base,
    read_generation_stable_executable_snapshot,
)
from src.services.workspace_release_runtime import (
    WorkspaceReleaseDescriptor,
    WorkspaceReleaseRuntimeError,
)
from src.services.workspace_release_storage import WorkspaceReleaseStorage

PREPARED_EVIDENCE_SCHEMA = "bifrost.workspace-release-prepared/v3"
SmokeRunner = Callable[[dict[str, bytes], str, str], Awaitable[dict[str, Any]]]
ProgressReporter = Callable[[str, int, int | None, float | None], Awaitable[None]]


class WorkspaceReleasePreparationError(RuntimeError):
    """A reviewed candidate could not be proven safe to materialize."""


def prepared_activation_challenge(
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild the exact authorization challenge from durable prepared proof."""
    return activation_challenge(
        artifact_id=str(evidence["artifact_id"]),
        candidate_id=str(evidence["candidate_id"]),
        workspace_release_id=str(evidence["release_id"]),
        prepared_evidence_id=str(evidence["evidence_id"]),
        effective_manifest_id=str(evidence["effective_manifest_id"]),
        governed_manifest_id=str(evidence["governed_manifest_id"]),
        effective_registration_manifest_id=str(
            evidence["effective_registration_manifest_id"]
        ),
        risk_class=str(evidence["risk_class"]),
        computed_effects=evidence["computed_effects"],
        policy_version=str(evidence["policy_version"]),
        protected_source=evidence["protected_source"],
    )


def _runtime_prefix(organization_id: UUID, release_id: str) -> str:
    digest = release_id.removeprefix("sha256:")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise WorkspaceReleasePreparationError("artifact release_id is invalid")
    return f"_workspace_releases/{organization_id}/{digest}/files/"


def _compile_files(files: dict[str, bytes]) -> None:
    for path, raw in sorted(files.items()):
        try:
            source = raw.decode("utf-8")
            compile(source, path, "exec")
        except (UnicodeDecodeError, SyntaxError) as exc:
            raise WorkspaceReleasePreparationError(
                f"candidate compile failed for {path}: {exc}"
            ) from exc


async def isolated_candidate_import_smoke(
    files: dict[str, bytes], entry_path: str, entry_function: str
) -> dict[str, Any]:
    """Import from an exact tree plus the trusted SDK; no `_repo` path exists."""
    smoke_script = Path(__file__).with_name("workspace_release_smoke.py")
    platform_root = Path(__file__).resolve().parents[2]
    sdk_package = platform_root / "bifrost"
    effects_contract = platform_root / "_bifrost_workspace_effects.py"
    with tempfile.TemporaryDirectory(prefix="bifrost-workspace-release-") as temp:
        root = Path(temp)
        for path, raw in sorted(files.items()):
            target = root.joinpath(*path.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(smoke_script),
            str(root),
            entry_path,
            entry_function,
            str(sdk_package),
            str(effects_contract),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise WorkspaceReleasePreparationError(
                "candidate import smoke exceeded 30 seconds"
            ) from exc
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[-2000:]
            raise WorkspaceReleasePreparationError(
                "candidate import smoke failed without `_repo` fallback"
                + (f": {detail}" if detail else "")
            )
        try:
            result = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkspaceReleasePreparationError(
                "candidate import smoke returned invalid evidence"
            ) from exc
        if (
            result.get("imported") is not True
            or result.get("function_callable") is not True
        ):
            raise WorkspaceReleasePreparationError(
                "candidate import smoke did not prove the entry function"
            )
        result.pop("workspace_source_root", None)
        result["source"] = "immutable_candidate_tree"
        return result


def _read_closure_zip(raw: bytes, expected: dict[str, str]) -> dict[str, bytes]:
    if len(expected) > MAX_CLOSURE_FILES:
        raise WorkspaceReleasePreparationError("artifact closure exceeds file limit")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != set(expected):
                raise WorkspaceReleasePreparationError(
                    "artifact source archive does not match its closure"
                )
            files: dict[str, bytes] = {}
            total = 0
            for name in names:
                if name.startswith("/") or ".." in name.replace("\\", "/").split("/"):
                    raise WorkspaceReleasePreparationError(
                        "artifact source archive contains an unsafe path"
                    )
                content = archive.read(name)
                total += len(content)
                if total > MAX_CLOSURE_BYTES:
                    raise WorkspaceReleasePreparationError(
                        "artifact closure exceeds byte limit"
                    )
                if sha256_bytes(content) != expected[name]:
                    raise WorkspaceReleasePreparationError(
                        f"artifact source hash mismatch for {name}"
                    )
                files[name] = content
            return files
    except zipfile.BadZipFile as exc:
        raise WorkspaceReleasePreparationError(
            "artifact source archive is invalid"
        ) from exc


class WorkspaceReleaseMaterializer:
    """CAS-check, stage, verify, and smoke one reviewed release artifact."""

    def __init__(
        self,
        db: AsyncSession,
        organization_id: UUID,
        *,
        repo_storage: RepoStorage | None = None,
        artifact_storage_factory: Callable[
            [UUID, str], WorkspacePromotionArtifactStorage
        ] = WorkspacePromotionArtifactStorage,
        release_storage_factory: Callable[
            [str], WorkspaceReleaseStorage
        ] = WorkspaceReleaseStorage,
        smoke_runner: SmokeRunner = isolated_candidate_import_smoke,
        workspace_generation: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.repo_storage = repo_storage or RepoStorage()
        self.artifact_storage_factory = artifact_storage_factory
        self.release_storage_factory = release_storage_factory
        self.smoke_runner = smoke_runner
        if workspace_generation is None:
            from src.core.module_cache import get_workspace_generation

            workspace_generation = get_workspace_generation
        self.workspace_generation = workspace_generation

    async def prepare(
        self,
        artifact_id: UUID,
        candidate_id: str,
        user_id: UUID,
        *,
        report: ProgressReporter | None = None,
    ) -> tuple[WorkspacePromotionRelease, dict[str, Any]]:
        artifact = await self._artifact(artifact_id, candidate_id)
        manifest = dict(artifact.manifest or {})
        self._validate_manifest(artifact, manifest)
        if report:
            await report("Resolving immutable Workspace base", 0, None, 5)
        base_files = await self._base_files(artifact)
        content_storage = self.artifact_storage_factory(
            self.organization_id, str(artifact.content_id)
        )
        if artifact.source_artifact_key != content_storage.source_artifact_key:
            raise WorkspaceReleasePreparationError(
                "artifact source storage key is not content-addressed"
            )
        closure_hashes = {item["path"]: item["sha256"] for item in manifest["closure"]}
        closure_files = _read_closure_zip(
            await content_storage.read_source(), closure_hashes
        )
        projection_paths = [
            {
                "path": path,
                "base_sha256": (
                    sha256_bytes(base_files[path]) if path in base_files else None
                ),
                "target_sha256": manifest["effective_files"][path],
            }
            for path in manifest["governed_paths"]
        ]
        effective = dict(sorted({**base_files, **closure_files}.items()))
        self._validate_effective(manifest, effective)
        if report:
            await report("Compiling exact Workspace release", 0, len(effective), 15)
        _compile_files(effective)
        runtime_prefix = _runtime_prefix(self.organization_id, str(artifact.release_id))
        release_storage = self.release_storage_factory(runtime_prefix)
        if report:
            await report(
                "Materializing immutable Workspace release", 0, len(effective), 25
            )
        await release_storage.write_many(effective)
        readback = await release_storage.read_many(sorted(effective))
        self._validate_effective(manifest, readback)
        risk_class = str(manifest["risk_class"])
        validation_smokes: list[dict[str, Any]] = []
        if risk_class == "R0":
            if report:
                await report(
                    "Running candidate-backed import integrity checks",
                    len(effective),
                    len(effective),
                    85,
                )
            selected_smoke: dict[str, Any] | None = None
            for target in manifest["validation_targets"]:
                smoke = await self.smoke_runner(
                    readback,
                    target["path"],
                    target["function"],
                )
                expected = {
                    "entry_path": target["path"],
                    "entry_function": target["function"],
                    "imported": True,
                    "function_callable": True,
                    "source": "immutable_candidate_tree",
                }
                if any(smoke.get(key) != value for key, value in expected.items()):
                    raise WorkspaceReleasePreparationError(
                        "candidate import integrity check did not prove affected "
                        f"executable {target['path']}::{target['function']}"
                    )
                bound_smoke = {
                    **smoke,
                    "entity_type": target["entity_type"],
                    "relation": target["relation"],
                }
                validation_smokes.append(bound_smoke)
                if target["relation"] == "selected_entry":
                    selected_smoke = bound_smoke
            if selected_smoke is None:
                raise WorkspaceReleasePreparationError(
                    "candidate validation did not prove the selected entry"
                )
            import_validation = {
                "state": "succeeded",
                "selected": selected_smoke,
                "targets": validation_smokes,
            }
            effect_execution = "reviewed_canary_required"
        else:
            import_validation = {
                "state": "not_performed",
                "reason": "non_r0_source_is_not_executed_during_prepare",
            }
            effect_execution = "not_performed"
        now = datetime.now(timezone.utc)
        effects = list(manifest["computed_effects"])
        evidence = {
            "schema_version": PREPARED_EVIDENCE_SCHEMA,
            "artifact_id": str(artifact.id),
            "candidate_id": artifact.candidate_id,
            "content_id": artifact.content_id,
            "release_id": artifact.release_id,
            "base_release_id": artifact.base_release_id,
            "base_manifest_id": artifact.base_manifest_id,
            "effective_manifest_id": artifact.effective_manifest_id,
            "effective_files": manifest["effective_files"],
            "governed_paths": manifest["governed_paths"],
            "governed_manifest_id": manifest["governed_manifest_id"],
            "effective_registration_manifest_id": manifest[
                "effective_registration_manifest_id"
            ],
            "risk_class": risk_class,
            "policy_version": str(manifest["policy_version"]),
            "computed_effects": effects,
            "computed_effects_id": computed_effects_id(effects),
            "protected_source": manifest["protected_source"],
            "runtime_storage_prefix": runtime_prefix,
            "file_count": len(readback),
            "total_bytes": sum(map(len, readback.values())),
            "compile": {"succeeded": True, "file_count": len(readback)},
            "import_validation": import_validation,
            "effect_execution": effect_execution,
            "projection_paths": projection_paths,
            "prepared_at": now.isoformat(),
        }
        evidence["evidence_id"] = canonical_digest(evidence)
        release, evidence = await self._record_prepared(
            artifact, user_id, evidence, now
        )
        if report:
            await report(
                "Workspace release prepared", len(effective), len(effective), 100
            )
        return release, evidence

    async def _artifact(
        self, artifact_id: UUID, candidate_id: str
    ) -> WorkspacePromotionArtifact:
        artifact = (
            await self.db.execute(
                select(WorkspacePromotionArtifact).where(
                    WorkspacePromotionArtifact.id == artifact_id,
                    WorkspacePromotionArtifact.organization_id == self.organization_id,
                )
            )
        ).scalar_one_or_none()
        if artifact is None:
            raise WorkspaceReleasePreparationError("release artifact does not exist")
        if artifact.candidate_id != candidate_id:
            raise WorkspaceReleasePreparationError(
                "candidate_id does not match artifact"
            )
        if (
            artifact.target_kind != "workspace"
            or artifact.schema_version != PROMOTION_BUNDLE_SCHEMA_V2
        ):
            raise WorkspaceReleasePreparationError(
                "local-only draft artifacts cannot be prepared for Live"
            )
        if artifact.expires_at <= datetime.now(timezone.utc):
            raise WorkspaceReleasePreparationError("release artifact has expired")
        if artifact.artifact_state == "invalid":
            raise WorkspaceReleasePreparationError("release artifact is invalid")
        child = (
            await self.db.execute(
                select(WorkspacePromotionArtifact.id).where(
                    WorkspacePromotionArtifact.organization_id == self.organization_id,
                    WorkspacePromotionArtifact.supersedes_artifact_id == artifact.id,
                )
            )
        ).scalar_one_or_none()
        if child is not None:
            raise WorkspaceReleasePreparationError("release artifact is superseded")
        return artifact

    @staticmethod
    def _validate_manifest(
        artifact: WorkspacePromotionArtifact, manifest: dict[str, Any]
    ) -> None:
        if manifest.get("schema_version") != PROMOTION_ARTIFACT_SCHEMA:
            raise WorkspaceReleasePreparationError(
                "artifact manifest schema is invalid"
            )
        bindings = {
            "content_id": artifact.content_id,
            "closure_id": artifact.closure_id,
            "release_id": artifact.release_id,
            "base_release_id": artifact.base_release_id,
            "base_manifest_id": artifact.base_manifest_id,
            "effective_manifest_id": artifact.effective_manifest_id,
        }
        if any(manifest.get(key) != value for key, value in bindings.items()):
            raise WorkspaceReleasePreparationError(
                "artifact row does not match its immutable manifest"
            )
        protected_source = manifest.get("protected_source")
        registration = manifest.get("registration")
        registration_intent_fingerprint = (
            registration.get("intent_fingerprint")
            if isinstance(registration, dict)
            else None
        )
        if (
            not isinstance(protected_source, dict)
            or protected_source.get("commit_sha") != artifact.source_revision
            or protected_source.get("tree_sha") != artifact.source_tree_sha
            or not isinstance(registration, dict)
            or not isinstance(registration_intent_fingerprint, str)
            or registration_intent_fingerprint
            != artifact.registration_intent_fingerprint
            or registration.get("state_fingerprint")
            != artifact.registration_state_fingerprint
            or manifest.get("effective_registration_manifest_id")
            != artifact.effective_registration_manifest_id
        ):
            raise WorkspaceReleasePreparationError(
                "artifact provenance or registration binding is invalid"
            )
        if _canonical_candidate(manifest) != artifact.candidate_id:
            raise WorkspaceReleasePreparationError("artifact candidate_id is invalid")
        entry = manifest.get("entry")
        closure = manifest.get("closure")
        if not isinstance(entry, dict) or not isinstance(closure, list):
            raise WorkspaceReleasePreparationError("artifact closure is invalid")
        closure_hashes = {
            item.get("path"): item.get("sha256")
            for item in closure
            if isinstance(item, dict)
        }
        cohort_paths = manifest.get("cohort_paths") or []
        expected_closure_id = (
            workspace_cohort_closure_id(entry, closure_hashes, cohort_paths)
            if cohort_paths
            else workspace_closure_id(entry, closure_hashes)
        )
        if (
            len(closure_hashes) != len(closure)
            or expected_closure_id != artifact.closure_id
            or workspace_content_id(entry, str(artifact.closure_id))
            != artifact.content_id
        ):
            raise WorkspaceReleasePreparationError(
                "artifact content identity is invalid"
            )
        release_payload = {
            key: manifest[key]
            for key in (
                "organization_id",
                "base_release_id",
                "base_manifest_id",
                "effective_manifest_id",
                "effective_files",
                "governed_paths",
                "governed_manifest_id",
                "effective_registration_manifest_id",
                "effective_registrations",
                "entry",
                "validation_targets",
                "risk_class",
                "computed_effects",
                "protected_source",
            )
        }
        release_payload["registration_intent_fingerprint"] = (
            registration_intent_fingerprint
        )
        if cohort_paths:
            release_payload.update(
                {
                    "source_release_id": manifest.get("source_release_id"),
                    "source_release_paths": manifest.get("source_release_paths"),
                    "cohort_paths": cohort_paths,
                }
            )
        if workspace_release_id(release_payload) != artifact.release_id:
            raise WorkspaceReleasePreparationError("artifact release_id is invalid")
        effective_files = manifest.get("effective_files")
        if (
            not isinstance(effective_files, dict)
            or not effective_files
            or len(effective_files) > MAX_SNAPSHOT_FILES
            or any(
                not isinstance(path, str)
                or not _is_executable_python_path(path)
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
                for path, digest in effective_files.items()
            )
            or workspace_manifest_id(effective_files) != artifact.effective_manifest_id
        ):
            raise WorkspaceReleasePreparationError(
                "artifact effective executable tree is invalid"
            )
        governed_paths = manifest.get("governed_paths")
        if (
            not isinstance(governed_paths, list)
            or not governed_paths
            or governed_paths != sorted(governed_paths)
            or len(governed_paths) != len(set(governed_paths))
            or any(path not in effective_files for path in governed_paths)
        ):
            raise WorkspaceReleasePreparationError(
                "artifact governed path manifest is invalid"
            )
        if workspace_manifest_id(
            {path: effective_files[path] for path in governed_paths}
        ) != manifest.get("governed_manifest_id"):
            raise WorkspaceReleasePreparationError(
                "artifact governed manifest digest is invalid"
            )
        registrations = manifest.get("effective_registrations")
        if not isinstance(registrations, dict) or any(
            not isinstance(row, dict) or row.get("path") not in governed_paths
            for row in registrations.values()
        ):
            raise WorkspaceReleasePreparationError(
                "artifact registration is outside its governed paths"
            )
        effects = manifest.get("computed_effects")
        effective_registrations = manifest.get("effective_registrations")
        if (
            artifact.risk_class not in {"R0", "R1", "R2"}
            or manifest.get("risk_class") != artifact.risk_class
            or not isinstance(effects, list)
            or not effects
            or any(not isinstance(effect, str) or not effect for effect in effects)
            or effects != sorted(set(effects))
            or not isinstance(effective_registrations, dict)
            or (
                "R2"
                if cohort_paths
                else _promotion_risk_class(effects, effective_registrations.values())
            )
            != artifact.risk_class
        ):
            raise WorkspaceReleasePreparationError(
                "artifact risk class does not match its explicit effects"
            )
        diagnostics = manifest.get("diagnostics")
        if not isinstance(diagnostics, list) or any(
            not isinstance(item, dict) or item.get("severity") == "blocker"
            for item in diagnostics
        ):
            raise WorkspaceReleasePreparationError(
                "artifact diagnostics do not prove a blocker-free release"
            )
        validation_targets = manifest.get("validation_targets")
        entry_key = (entry.get("path"), entry.get("function"))
        if (
            not isinstance(validation_targets, list)
            or not validation_targets
            or validation_targets
            != sorted(
                validation_targets,
                key=lambda item: (
                    item.get("path", "") if isinstance(item, dict) else "",
                    item.get("function", "") if isinstance(item, dict) else "",
                ),
            )
            or len(
                {
                    (item.get("path"), item.get("function"))
                    for item in validation_targets
                    if isinstance(item, dict)
                }
            )
            != len(validation_targets)
            or any(
                not isinstance(item, dict)
                or set(item) != {"path", "function", "entity_type", "relation"}
                or item.get("path") not in effective_files
                or item.get("entity_type") not in {"workflow", "tool", "data_provider"}
                or item.get("relation") not in {"selected_entry", "affected_executable"}
                for item in validation_targets
            )
        ):
            raise WorkspaceReleasePreparationError(
                "artifact affected-executable validation targets are invalid"
            )
        selected = [
            item
            for item in validation_targets
            if item.get("relation") == "selected_entry"
        ]
        if (
            len(selected) != 1
            or (selected[0].get("path"), selected[0].get("function")) != entry_key
            or selected[0].get("entity_type") != "workflow"
        ):
            raise WorkspaceReleasePreparationError(
                "artifact does not bind exactly one selected workflow entry"
            )

    async def _base_files(
        self, artifact: WorkspacePromotionArtifact
    ) -> dict[str, bytes]:
        active = (
            await self.db.execute(
                select(WorkspacePromotionRelease, WorkspacePromotionArtifact)
                .join(
                    WorkspacePromotionArtifact,
                    WorkspacePromotionArtifact.id
                    == WorkspacePromotionRelease.artifact_id,
                )
                .where(
                    WorkspacePromotionRelease.activation_state == "live",
                )
                .limit(2)
            )
        ).all()
        if len(active) > 1:
            raise WorkspaceReleasePreparationError(
                "organization has more than one active Workspace release"
            )
        if active:
            release, base_artifact = active[0]
            try:
                descriptor = WorkspaceReleaseDescriptor.from_rows(
                    release, base_artifact
                )
            except WorkspaceReleaseRuntimeError as exc:
                raise WorkspaceReleasePreparationError(str(exc)) from exc
            if descriptor.release_id != artifact.base_release_id:
                raise WorkspaceReleasePreparationError(
                    "active Workspace release changed after preview"
                )
            immutable_files = await self.release_storage_factory(
                descriptor.runtime_storage_prefix
            ).read_many(sorted(descriptor.source_hashes))
            immutable_hashes = {
                path: sha256_bytes(raw) for path, raw in sorted(immutable_files.items())
            }
            if (
                immutable_hashes != descriptor.source_hashes
                or workspace_manifest_id(immutable_hashes)
                != descriptor.effective_manifest_id
            ):
                raise WorkspaceReleasePreparationError(
                    "active Workspace base failed immutable readback"
                )
            try:
                files = await read_generation_stable_executable_snapshot(
                    self.repo_storage, self.workspace_generation
                )
            except ValueError as exc:
                raise WorkspaceReleasePreparationError(str(exc)) from exc
            files = overlay_governed_base(
                files, immutable_files, descriptor.governed_paths
            )
            hashes = {path: sha256_bytes(raw) for path, raw in sorted(files.items())}
            if workspace_manifest_id(hashes) != artifact.base_manifest_id:
                raise WorkspaceReleasePreparationError(
                    "hybrid Workspace base changed after preview"
                )
            return files

        try:
            files = await read_generation_stable_executable_snapshot(
                self.repo_storage, self.workspace_generation
            )
        except ValueError as exc:
            raise WorkspaceReleasePreparationError(str(exc)) from exc
        hashes = {path: sha256_bytes(raw) for path, raw in sorted(files.items())}
        if (
            repo_v1_release_id(hashes) != artifact.base_release_id
            or workspace_manifest_id(hashes) != artifact.base_manifest_id
        ):
            raise WorkspaceReleasePreparationError(
                "durable Workspace base changed after preview"
            )
        return files

    @staticmethod
    def _validate_effective(manifest: dict[str, Any], files: dict[str, bytes]) -> None:
        hashes = {path: sha256_bytes(raw) for path, raw in sorted(files.items())}
        if hashes != manifest.get("effective_files"):
            raise WorkspaceReleasePreparationError(
                "materialized files do not match the effective manifest"
            )
        if workspace_manifest_id(hashes) != manifest.get("effective_manifest_id"):
            raise WorkspaceReleasePreparationError(
                "materialized effective manifest digest is invalid"
            )

    async def _record_prepared(
        self,
        artifact: WorkspacePromotionArtifact,
        user_id: UUID,
        evidence: dict[str, Any],
        prepared_at: datetime,
    ) -> tuple[WorkspacePromotionRelease, dict[str, Any]]:
        release = (
            await self.db.execute(
                select(WorkspacePromotionRelease).where(
                    WorkspacePromotionRelease.organization_id == self.organization_id,
                    WorkspacePromotionRelease.artifact_id == artifact.id,
                )
            )
        ).scalar_one_or_none()
        if release is None:
            release = WorkspacePromotionRelease(
                organization_id=self.organization_id,
                artifact_id=artifact.id,
                idempotency_key=f"prepare:{artifact.candidate_id}",
                activation_state="prepared",
                lock_state="not_queued",
                created_by=user_id,
            )
            self.db.add(release)
        elif release.activation_state != "prepared":
            raise WorkspaceReleasePreparationError(
                "artifact release has advanced beyond preparation"
            )
        elif release.prepared_evidence is not None:
            existing = dict(release.prepared_evidence)
            stable_keys = set(evidence) - {"prepared_at", "evidence_id"}
            if any(existing.get(key) != evidence.get(key) for key in stable_keys):
                raise WorkspaceReleasePreparationError(
                    "existing prepared evidence differs from immutable readback"
                )
            return release, existing
        release.prepared_evidence = evidence
        release.prepared_at = prepared_at
        release.error_code = None
        release.error_message = None
        await self.db.commit()
        await self.db.refresh(release)
        return release, evidence
