"""Preview-only compiler for immutable rapid Workspace promotion artifacts."""

from __future__ import annotations

import ast
import asyncio
import base64
import binascii
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost.promotion import (
    MAX_CLOSURE_BYTES,
    MAX_CLOSURE_FILES,
    MAX_SNAPSHOT_FILES,
    PromotionBundleError,
    normalize_workspace_path,
    reject_path_collisions,
    sha256_bytes,
    snapshot_id,
)
from bifrost.workspace_impact import (
    WORKSPACE_IMPORT_ROOTS,
    analyze_workspace_impact,
    reverse_edges,
    transitive_distances,
)
from bifrost.workspace_release import (
    WORKSPACE_RELEASE_CONTENT_SCHEMA,
    canonical_digest,
    repo_v1_release_id,
    workspace_closure_id,
    workspace_content_id,
    workspace_manifest_id,
    workspace_registration_manifest_id,
    workspace_release_id,
)
from src.models.contracts.workspace_promotions import (
    PromotionClosureMember,
    PromotionDiagnostic,
    PromotionEntry,
    PromotionRegistrationEvidence,
    PromotionSourceEvidence,
    WorkspacePromotionDraftRequest,
    WorkspacePromotionDraftResponse,
    WorkspacePromotionArtifactResponse,
    WorkspacePromotionPreviewRequest,
    WorkspacePromotionPreviewResponse,
)
from src.models.orm.workspace_promotions import (
    WorkspacePromotionArtifact,
    WorkspacePromotionRelease,
)
from src.models.orm.workflows import Workflow
from src.services.audit import emit_audit
from src.services.platform_commit_writer import (
    PlatformCommitError,
    PlatformCommitWriter,
)
from src.services.repo_storage import RepoStorage
from src.services.workflow_registration import (
    WorkspaceRegistrationCandidate,
    find_workspace_workflow,
    plan_workspace_registrations,
    resolve_workflow_registration_id,
)
from src.services.workspace_promotion_storage import WorkspacePromotionArtifactStorage

PROMOTION_PREVIEW_POLICY = "workspace-release-artifact/2026-08-19"
PROMOTION_BUNDLE_SCHEMA_V2 = "bifrost.workspace-promotion-bundle/v2"
PROMOTION_ARTIFACT_SCHEMA = "bifrost.workspace-release-artifact/v1"
ARTIFACT_TTL = timedelta(days=7)
DRAFT_ARTIFACT_TTL = timedelta(hours=24)
DRAFT_UPLOAD_SCHEMA = "bifrost.workspace-draft-upload/v1"
DRAFT_ARTIFACT_SCHEMA = "bifrost.workspace-draft-artifact/v1"
R0_EFFECTS = {"bifrost.read"}
REQUIRED_R0_BOUNDS = {
    "max_duration_seconds",
    "max_external_calls",
    "max_records_read",
    "max_output_bytes",
}
_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        rb"(?i)(?:api[_-]?key|client[_-]?secret|password)\s*=\s*['\"][^'\"\s]{12,}['\"]"
    ),
)


class WorkspacePromotionInvalid(ValueError):
    """Submitted bytes do not form one safe, coherent preview artifact."""


async def acquire_workspace_promotion_artifact_lock(
    db: AsyncSession, organization_id: UUID
) -> None:
    """Serialize candidate/supersession/retention changes for one organization."""

    await db.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtext('bifrost:workspace-promotion-artifacts:' || :organization_id)"
            ")"
        ),
        {"organization_id": str(organization_id)},
    )


@dataclass(frozen=True)
class _BaseSnapshot:
    release_id: str
    manifest_id: str
    files: dict[str, bytes]
    hashes: dict[str, str]
    registrations: dict[str, dict[str, Any]]


def _digest(payload: Any) -> str:
    return canonical_digest(payload)


def _manifest_id(files: dict[str, str]) -> str:
    return workspace_manifest_id(files)


def _repo_v1_release_id(files: dict[str, str]) -> str:
    return repo_v1_release_id(files)


def _closure_id(entry: PromotionEntry, hashes: dict[str, str]) -> str:
    return workspace_closure_id(entry.model_dump(), hashes)


def _content_id(entry: PromotionEntry, closure_id: str) -> str:
    return workspace_content_id(entry.model_dump(), closure_id)


def _is_executable_python_path(path: str) -> bool:
    return path.endswith(".py") and path.split("/", 1)[0] in WORKSPACE_IMPORT_ROOTS


def _allocated_registration_id(
    organization_id: UUID, path: str, function_name: str
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"bifrost-workspace:{organization_id}:{path}::{function_name}",
    )


def _is_r0_effect(effect: str) -> bool:
    kind = effect.split(":", 1)[0]
    return kind in R0_EFFECTS


def _literal_keyword(call: ast.Call, name: str, default: Any = None) -> Any:
    for keyword in call.keywords:
        if keyword.arg == name:
            try:
                return ast.literal_eval(keyword.value)
            except (ValueError, TypeError) as exc:
                raise WorkspacePromotionInvalid(
                    f"decorator keyword {name!r} must be a literal"
                ) from exc
    return default


def _keyword_node(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _decorator_name(value: ast.expr) -> str | None:
    target = value.func if isinstance(value, ast.Call) else value
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _effect_declarations(node: ast.expr | None) -> list[dict[str, Any]]:
    if node is None:
        raise WorkspacePromotionInvalid(
            "workflow effects must be explicitly declared; None is undeclared"
        )
    if not isinstance(node, (ast.List, ast.Tuple)):
        raise WorkspacePromotionInvalid("workflow effects must be a literal sequence")
    values: list[dict[str, Any]] = []
    for item in node.elts:
        if isinstance(item, ast.Dict):
            try:
                value = ast.literal_eval(item)
            except (ValueError, TypeError) as exc:
                raise WorkspacePromotionInvalid(
                    "workflow effect mappings must contain only literals"
                ) from exc
        elif isinstance(item, ast.Call) and _decorator_name(item) == "WorkflowEffect":
            if item.args or any(keyword.arg is None for keyword in item.keywords):
                raise WorkspacePromotionInvalid(
                    "WorkflowEffect declarations require literal keyword arguments"
                )
            try:
                value = {
                    keyword.arg: ast.literal_eval(keyword.value)
                    for keyword in item.keywords
                    if keyword.arg is not None
                }
            except (ValueError, TypeError) as exc:
                raise WorkspacePromotionInvalid(
                    "WorkflowEffect declarations require literal keyword arguments"
                ) from exc
        else:
            raise WorkspacePromotionInvalid(
                "each workflow effect must be a literal mapping or WorkflowEffect"
            )
        if not isinstance(value, dict):
            raise WorkspacePromotionInvalid("workflow effect must be a mapping")
        values.append(value)
    return values


def _normalize_effects(node: ast.expr | None) -> list[str]:
    result: set[str] = set()
    for value in _effect_declarations(node):
        if isinstance(value, dict) and isinstance(value.get("kind"), str):
            kind = value["kind"]
        else:
            raise WorkspacePromotionInvalid(
                "each workflow effect must be a literal mapping with a string kind"
            )
        unknown = set(value) - {"kind", "target"}
        if unknown:
            raise WorkspacePromotionInvalid(
                "workflow effect contains unknown fields: " + ", ".join(sorted(unknown))
            )
        target = value.get("target")
        if kind.startswith("integration.") and (
            not isinstance(target, str) or not target.strip()
        ):
            raise WorkspacePromotionInvalid(
                f"workflow effect {kind!r} requires a literal integration target"
            )
        if target is not None and not isinstance(target, str):
            raise WorkspacePromotionInvalid("workflow effect target must be a string")
        result.add(f"{kind}:{target}" if target else kind)
    return sorted(result)


def _bounds_declaration(node: ast.expr | None, field_name: str) -> dict[str, int]:
    if node is None:
        return {}
    if isinstance(node, ast.Dict):
        try:
            value = ast.literal_eval(node)
        except (ValueError, TypeError) as exc:
            raise WorkspacePromotionInvalid(
                f"workflow {field_name} must contain only literals"
            ) from exc
    elif isinstance(node, ast.Call) and _decorator_name(node) == "WorkflowBounds":
        if node.args or any(keyword.arg is None for keyword in node.keywords):
            raise WorkspacePromotionInvalid(
                f"WorkflowBounds for {field_name} requires literal keyword arguments"
            )
        try:
            value = {
                keyword.arg: ast.literal_eval(keyword.value)
                for keyword in node.keywords
                if keyword.arg is not None
            }
        except (ValueError, TypeError) as exc:
            raise WorkspacePromotionInvalid(
                f"WorkflowBounds for {field_name} requires literal keyword arguments"
            ) from exc
    else:
        raise WorkspacePromotionInvalid(
            f"workflow {field_name} must be a literal mapping or WorkflowBounds"
        )
    if not isinstance(value, dict) or any(
        not isinstance(key, str)
        or not isinstance(item, int)
        or isinstance(item, bool)
        or item <= 0
        for key, item in value.items()
    ):
        raise WorkspacePromotionInvalid(
            f"workflow {field_name} must contain positive integer literals"
        )
    return dict(sorted(value.items()))


def _entry_metadata(raw: bytes, path: str, function_name: str) -> dict[str, Any]:
    try:
        tree = ast.parse(raw.decode("utf-8"), filename=path)
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise WorkspacePromotionInvalid(
            f"cannot parse selected workflow: {exc}"
        ) from exc
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    if len(matches) != 1:
        raise WorkspacePromotionInvalid(
            f"expected exactly one function named {function_name!r} in {path}"
        )
    node = matches[0]
    decorated = [
        item
        for item in node.decorator_list
        if _decorator_name(item) in {"workflow", "tool", "data_provider"}
    ]
    if len(decorated) != 1 or not isinstance(decorated[0], ast.Call):
        raise WorkspacePromotionInvalid(
            "rapid preview requires exactly one called @workflow decorator"
        )
    decorator = decorated[0]
    kind = _decorator_name(decorator)
    if kind != "workflow":
        raise WorkspacePromotionInvalid(
            "rapid preview currently supports manually invoked workflows only"
        )
    effects = _normalize_effects(_keyword_node(decorator, "effects"))
    bounds = _bounds_declaration(
        _keyword_node(decorator, "enforced_bounds"), "enforced_bounds"
    )
    requested_bounds = _bounds_declaration(
        _keyword_node(decorator, "requested_bounds"), "requested_bounds"
    )
    requested_id = _literal_keyword(decorator, "id")
    name = _literal_keyword(decorator, "name", function_name)
    if requested_id is not None and not isinstance(requested_id, str):
        raise WorkspacePromotionInvalid("workflow id must be a literal string")
    if not isinstance(name, str):
        raise WorkspacePromotionInvalid("workflow name must be a literal string")
    return {
        "type": kind,
        "name": name,
        "requested_id": requested_id,
        "effects": effects,
        "bounds": bounds,
        "requested_bounds": requested_bounds,
    }


def _static_effects(
    files: dict[str, bytes],
) -> tuple[list[str], list[PromotionDiagnostic]]:
    effects: set[str] = set()
    diagnostics: list[PromotionDiagnostic] = []
    network_roots = {"requests", "httpx", "aiohttp", "socket", "urllib3"}
    process_roots = {"subprocess"}
    for path, raw in files.items():
        try:
            tree = ast.parse(raw.decode("utf-8"), filename=path)
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise WorkspacePromotionInvalid(f"cannot parse {path}: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".", 1)[0]}
            else:
                roots = set()
            if roots & network_roots:
                effects.add("network.unknown")
            if roots & process_roots:
                effects.add("process.execute")
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else None
                attr = node.func.attr if isinstance(node.func, ast.Attribute) else None
                if name in {"eval", "exec"}:
                    effects.add("dynamic_code.execute")
                if attr in {"system", "popen", "spawn", "run", "Popen"}:
                    effects.add("process.execute")
                if (name == "import_module" or attr == "import_module") and (
                    not node.args
                    or not isinstance(node.args[0], ast.Constant)
                    or not isinstance(node.args[0].value, str)
                ):
                    effects.add("dynamic_code.execute")
        if any(pattern.search(raw) for pattern in _SECRET_PATTERNS):
            diagnostics.append(
                PromotionDiagnostic(
                    code="secret_material_detected",
                    severity="blocker",
                    message="high-confidence secret material is present in source",
                    path=path,
                )
            )
    return sorted(effects), diagnostics


def _canonical_candidate(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _source_zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[path])
    return output.getvalue()


def _validate_closure_files(
    request: WorkspacePromotionPreviewRequest | WorkspacePromotionDraftRequest,
    files: dict[str, bytes],
) -> dict[str, bytes]:
    snapshot_files = {
        normalize_workspace_path(path): digest
        for path, digest in request.snapshot.files.items()
    }
    reject_path_collisions(snapshot_files)
    if snapshot_id(snapshot_files) != request.snapshot.snapshot_id:
        raise WorkspacePromotionInvalid(
            "snapshot_id does not match the submitted path/hash manifest"
        )
    declared: dict[str, str] = {}
    for item in request.snapshot.closure:
        path = normalize_workspace_path(item.path)
        if path in declared:
            raise WorkspacePromotionInvalid(f"duplicate closure path: {path}")
        declared[path] = item.sha256
    if set(files) != set(declared):
        raise WorkspacePromotionInvalid(
            "protected source paths do not match the declared closure"
        )
    if (
        len(files) > MAX_CLOSURE_FILES
        or sum(map(len, files.values())) > MAX_CLOSURE_BYTES
    ):
        raise WorkspacePromotionInvalid("submitted closure exceeds promotion limits")
    decoded: dict[str, bytes] = {}
    for path, raw in files.items():
        digest = sha256_bytes(raw)
        if declared[path] != digest or snapshot_files.get(path) != digest:
            raise WorkspacePromotionInvalid(f"content hash mismatch for {path}")
        decoded[path] = raw
    selected = normalize_workspace_path(request.entry.path)
    if selected not in decoded:
        raise WorkspacePromotionInvalid("selected workflow path is absent from closure")
    return decoded


def _decode_draft_closure(
    request: WorkspacePromotionDraftRequest,
) -> dict[str, bytes]:
    """Decode a bounded upload before any immutable object is written."""
    if len(request.snapshot.closure) > MAX_CLOSURE_FILES:
        raise WorkspacePromotionInvalid("submitted closure exceeds promotion limits")
    decoded: dict[str, bytes] = {}
    total = 0
    for item in request.snapshot.closure:
        path = normalize_workspace_path(item.path)
        if path in decoded:
            raise WorkspacePromotionInvalid(f"duplicate closure path: {path}")
        if item.content_base64 is None:
            raise WorkspacePromotionInvalid(
                f"draft closure content is required for {path}"
            )
        try:
            raw = base64.b64decode(item.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise WorkspacePromotionInvalid(
                f"draft closure content is not valid base64 for {path}"
            ) from exc
        total += len(raw)
        if total > MAX_CLOSURE_BYTES:
            raise WorkspacePromotionInvalid(
                "submitted closure exceeds promotion limits"
            )
        decoded[path] = raw
    return _validate_closure_files(request, decoded)


def _validate_draft_snapshot(
    request: WorkspacePromotionDraftRequest,
    base: _BaseSnapshot,
    closure_files: dict[str, bytes],
) -> None:
    """Prove graph analysis used the caller's complete executable source tree."""
    submitted = {
        normalize_workspace_path(path): digest
        for path, digest in request.snapshot.files.items()
    }
    if any(not _is_executable_python_path(path) for path in submitted):
        raise WorkspacePromotionInvalid(
            "draft snapshot may contain only executable authored Python source"
        )
    closure_hashes = {
        path: sha256_bytes(raw) for path, raw in sorted(closure_files.items())
    }
    effective = dict(sorted({**base.hashes, **closure_hashes}.items()))
    if submitted != effective:
        raise WorkspacePromotionInvalid(
            "draft snapshot does not match the current server base plus closure"
        )


def _canonical_impact_diagnostics(
    *,
    entry_path: str,
    base_files: dict[str, bytes],
    closure_files: dict[str, bytes],
) -> tuple[list[PromotionDiagnostic], set[str]]:
    effective_python = {
        path: raw
        for path, raw in {**base_files, **closure_files}.items()
        if path.endswith(".py") and path.split("/", 1)[0] in WORKSPACE_IMPORT_ROOTS
    }
    try:
        analysis = analyze_workspace_impact(effective_python)
    except PromotionBundleError as exc:
        raise WorkspacePromotionInvalid(str(exc)) from exc

    forward = set(transitive_distances(entry_path, analysis.edges))
    submitted = set(closure_files)
    missing = forward - submitted
    extras = submitted - forward
    if missing:
        raise WorkspacePromotionInvalid(
            "dependency closure is missing " + ", ".join(sorted(missing))
        )
    if extras:
        raise WorkspacePromotionInvalid(
            "closure contains unrelated paths: " + ", ".join(sorted(extras))
        )

    changed = {
        path
        for path, raw in closure_files.items()
        if base_files.get(path) != raw
    }
    reverse = reverse_edges(analysis.edges)
    affected = set(forward)
    for path in changed:
        affected.update(transitive_distances(path, reverse))

    diagnostics: list[PromotionDiagnostic] = []
    for path in sorted(affected):
        if unresolved := analysis.unresolved_imports.get(path):
            diagnostics.append(
                PromotionDiagnostic(
                    code="unresolved_repo_import",
                    severity="blocker",
                    message="unresolved repo-local imports: " + ", ".join(unresolved),
                    path=path,
                )
            )
        if ambiguous := analysis.ambiguous_references.get(path):
            diagnostics.append(
                PromotionDiagnostic(
                    code="ambiguous_workflow_reference",
                    severity="blocker",
                    message="workflow reference resolves ambiguously: "
                    + ", ".join(ambiguous),
                    path=path,
                )
            )
        if path in analysis.dynamic_importers:
            diagnostics.append(
                PromotionDiagnostic(
                    code="dynamic_import_unresolved",
                    severity="blocker",
                    message="computed dynamic import prevents complete impact proof",
                    path=path,
                )
            )
        if path in analysis.dynamic_reference_importers:
            diagnostics.append(
                PromotionDiagnostic(
                    code="dynamic_workflow_reference_unresolved",
                    severity="blocker",
                    message=(
                        "computed workflow reference prevents complete impact proof"
                    ),
                    path=path,
                )
            )

    for changed_path in sorted(changed):
        outside = sorted(
            set(transitive_distances(changed_path, reverse))
            - submitted
            - {changed_path}
        )
        if outside:
            diagnostics.append(
                PromotionDiagnostic(
                    code="shared_dependency_outside_candidate",
                    severity="blocker",
                    message=(
                        "changed source has reverse consumers outside the candidate: "
                        + ", ".join(outside[:20])
                    ),
                    path=changed_path,
                )
            )
    return diagnostics, forward


class WorkspacePromotionPreviewService:
    """Build and persist immutable release artifacts without activating them."""

    def __init__(
        self,
        db: AsyncSession,
        organization_id: UUID,
        *,
        repo_storage: RepoStorage | None = None,
        commit_writer: PlatformCommitWriter | None = None,
        workspace_generation: Callable[[], Awaitable[str]] | None = None,
        base_resolver: Callable[[], Awaitable[_BaseSnapshot]] | None = None,
    ):
        self.db = db
        self.organization_id = organization_id
        self.repo_storage = repo_storage or RepoStorage()
        self.commit_writer = commit_writer
        if workspace_generation is None:
            from src.core.module_cache import get_workspace_generation

            workspace_generation = get_workspace_generation
        self.workspace_generation = workspace_generation
        self.base_resolver = base_resolver

    async def _lock_artifact_creation(self) -> None:
        """Serialize immutable candidate creation and supersession per org."""

        await acquire_workspace_promotion_artifact_lock(
            self.db, self.organization_id
        )

    async def upload_draft(
        self, request: WorkspacePromotionDraftRequest, user_id: UUID
    ) -> WorkspacePromotionDraftResponse:
        """Persist safe local bytes with no provenance or Live authority."""
        if normalize_workspace_path(request.entry.path) != request.entry.path:
            raise WorkspacePromotionInvalid("entry path must be canonical POSIX form")
        files = _decode_draft_closure(request)
        base = await self._active_or_repo_base()
        _validate_draft_snapshot(request, base, files)
        metadata = _entry_metadata(
            files[request.entry.path], request.entry.path, request.entry.function
        )
        graph_diagnostics, _ = _canonical_impact_diagnostics(
            entry_path=request.entry.path,
            base_files=base.files,
            closure_files=files,
        )
        static_effects, diagnostics = _static_effects(files)
        diagnostics.extend(graph_diagnostics)
        if any(item.code == "secret_material_detected" for item in diagnostics):
            raise WorkspacePromotionInvalid(
                "high-confidence secret material detected; no artifact was stored"
            )
        declared_effects = metadata["effects"]
        computed_effects = sorted(set(declared_effects) | set(static_effects))
        undeclared = set(static_effects) - set(declared_effects)
        if undeclared:
            diagnostics.append(
                PromotionDiagnostic(
                    code="undeclared_static_effect",
                    severity="blocker",
                    message="source implies undeclared effects: "
                    + ", ".join(sorted(undeclared)),
                )
            )
        non_r0 = [effect for effect in computed_effects if not _is_r0_effect(effect)]
        if non_r0:
            diagnostics.append(
                PromotionDiagnostic(
                    code="draft_effect_not_allowed",
                    severity="blocker",
                    message="local-only drafts permit only R0 effects: "
                    + ", ".join(non_r0),
                )
            )
        missing_bounds = REQUIRED_R0_BOUNDS - set(metadata["bounds"])
        if missing_bounds:
            diagnostics.append(
                PromotionDiagnostic(
                    code="unenforced_resource_bounds",
                    severity="blocker",
                    message="missing enforced bounds: "
                    + ", ".join(sorted(missing_bounds)),
                )
            )
        closure_hashes = {
            path: sha256_bytes(raw) for path, raw in sorted(files.items())
        }
        closure_id = _closure_id(request.entry, closure_hashes)
        content_id = _content_id(request.entry, closure_id)
        if request.local_run is not None and (
            not request.local_run.succeeded
            or request.local_run.closure_id != closure_id
        ):
            diagnostics.append(
                PromotionDiagnostic(
                    code="local_run_evidence_mismatch",
                    severity="blocker",
                    message="local run evidence is not successful for this closure",
                )
            )
        blockers = [item for item in diagnostics if item.severity == "blocker"]
        if blockers:
            raise WorkspacePromotionInvalid(
                "draft validation failed: "
                + "; ".join(f"{item.code}: {item.message}" for item in blockers)
            )
        closure = [
            PromotionClosureMember(
                path=path,
                sha256=closure_hashes[path],
                size=len(raw),
                relation="selected" if path == request.entry.path else "dependency",
            )
            for path, raw in sorted(files.items())
        ]
        candidate_payload = {
            "schema_version": DRAFT_ARTIFACT_SCHEMA,
            "organization_id": str(self.organization_id),
            "target": "draft",
            "authority": "local_only",
            "activatable": False,
            "entry": request.entry.model_dump(),
            "content_id": content_id,
            "closure_id": closure_id,
            "snapshot_id": request.snapshot.snapshot_id,
            "closure": [item.model_dump() for item in closure],
            "declared_effects": declared_effects,
            "static_effects": static_effects,
            "computed_effects": computed_effects,
            "bounds": metadata["bounds"],
            "requested_bounds": metadata["requested_bounds"],
            "local_run": request.local_run.model_dump(mode="json")
            if request.local_run
            else None,
            "client": request.client.model_dump(),
            "policy_version": PROMOTION_PREVIEW_POLICY,
        }
        candidate_id = _canonical_candidate(candidate_payload)
        expires_at = datetime.now(timezone.utc) + DRAFT_ARTIFACT_TTL
        await self._lock_artifact_creation()
        artifact = await self._find_artifact(candidate_id)
        if artifact is None:
            storage = WorkspacePromotionArtifactStorage(
                self.organization_id, content_id
            )
            manifest_bytes = json.dumps(
                {
                    "schema": WORKSPACE_RELEASE_CONTENT_SCHEMA,
                    "content_id": content_id,
                    "closure_id": closure_id,
                    "entry": request.entry.model_dump(),
                    "files": closure_hashes,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            source_key = await storage.write_source(_source_zip(files))
            manifest_key = await storage.write_manifest(manifest_bytes)
            artifact = WorkspacePromotionArtifact(
                organization_id=self.organization_id,
                candidate_id=candidate_id,
                content_id=content_id,
                closure_id=closure_id,
                release_id=None,
                base_release_id=None,
                base_manifest_id=None,
                effective_manifest_id=None,
                effective_registration_manifest_id=None,
                registration_intent_fingerprint=None,
                registration_state_fingerprint=None,
                schema_version=DRAFT_UPLOAD_SCHEMA,
                target_kind="draft",
                entity_type="workflow",
                entry_path=request.entry.path,
                entry_function=request.entry.function,
                snapshot_id=request.snapshot.snapshot_id,
                source_revision=None,
                source_tree_sha=None,
                source_artifact_key=source_key,
                manifest_key=manifest_key,
                manifest=candidate_payload,
                risk_class="R0",
                disposition="review_required",
                artifact_state="previewed",
                policy_version=PROMOTION_PREVIEW_POLICY,
                created_by=user_id,
                expires_at=expires_at,
            )
            self.db.add(artifact)
            await self.db.flush()
        await emit_audit(
            self.db,
            "workspace_promotion.draft_uploaded",
            resource_type="workspace_promotion_artifact",
            resource_id=artifact.id,
            details={
                "candidate_id": candidate_id,
                "content_id": content_id,
                "closure_id": closure_id,
                "authority": "local_only",
                "activatable": False,
                "entry_path": request.entry.path,
                "entry_function": request.entry.function,
            },
            strict=True,
        )
        await self.db.commit()
        await self.db.refresh(artifact)
        lifecycle = (
            "expired"
            if artifact.expires_at <= datetime.now(timezone.utc)
            else "previewed"
        )
        return WorkspacePromotionDraftResponse(
            artifact_id=artifact.id,
            candidate_id=candidate_id,
            content_id=content_id,
            closure_id=closure_id,
            entry=request.entry,
            snapshot_id=request.snapshot.snapshot_id,
            closure=closure,
            declared_effects=declared_effects,
            computed_effects=computed_effects,
            bounds=metadata["bounds"],
            lifecycle_status=lifecycle,
            source_artifact_key=artifact.source_artifact_key,
            expires_at=artifact.expires_at,
            created_at=artifact.created_at,
        )

    async def preview(
        self, request: WorkspacePromotionPreviewRequest, user_id: UUID
    ) -> WorkspacePromotionPreviewResponse:
        if normalize_workspace_path(request.entry.path) != request.entry.path:
            raise WorkspacePromotionInvalid("entry path must be canonical POSIX form")
        protected_source, source_files = await self._read_protected_source(request)
        files = _validate_closure_files(request, source_files)
        base = await self._resolve_base(request)
        metadata = _entry_metadata(
            files[request.entry.path], request.entry.path, request.entry.function
        )
        impact_diagnostics, _ = _canonical_impact_diagnostics(
            entry_path=request.entry.path,
            base_files=base.files,
            closure_files=files,
        )

        static_effects, diagnostics = _static_effects(files)
        diagnostics.extend(impact_diagnostics)
        if any(item.code == "secret_material_detected" for item in diagnostics):
            raise WorkspacePromotionInvalid(
                "high-confidence secret material detected; no artifact was stored"
            )
        declared_effects = metadata["effects"]
        computed_effects = sorted(set(declared_effects) | set(static_effects))
        action_list, registry_diagnostics = await plan_workspace_registrations(
            self.db,
            self.organization_id,
            [
                WorkspaceRegistrationCandidate(
                    path=request.entry.path,
                    function_name=request.entry.function,
                    workflow_type="workflow",
                    name=metadata["name"],
                    requested_id=metadata["requested_id"],
                )
            ],
        )
        for item in registry_diagnostics:
            diagnostics.append(
                PromotionDiagnostic(
                    code="registration_conflict",
                    severity="blocker",
                    message=item["message"],
                    path=item.get("path"),
                )
            )
        for action in action_list:
            if action.get("action") != "create" or action.get("requested_id"):
                continue
            allocated_id = _allocated_registration_id(
                self.organization_id,
                action["path"],
                action["function_name"],
            )
            await resolve_workflow_registration_id(self.db, str(allocated_id), None)
            action["requested_id"] = str(allocated_id)
        registration_intent = action_list
        existing = await self._existing_workflow(
            request.entry.path, request.entry.function
        )
        registration_state = self._activation_state(existing)
        registration_intent_fingerprint = _digest(
            {
                "schema": "bifrost.workspace-registration-intent/v1",
                "actions": registration_intent,
            }
        )
        registration_state_fingerprint = _digest(
            {
                "schema": "bifrost.workspace-registration-state/v1",
                "state": registration_state,
            }
        )
        if existing is not None and (
            existing.organization_id is None
            or existing.type != "workflow"
            or existing.endpoint_enabled
            or existing.public_endpoint
            or existing.api_key_enabled
            or existing.access_level != "role_based"
        ):
            diagnostics.append(
                PromotionDiagnostic(
                    code="existing_activation_surface",
                    severity="blocker",
                    message=(
                        "existing global, tool/provider, endpoint, API-key, or "
                        "non-role-based "
                        "registration is not eligible for rapid promotion"
                    ),
                    path=request.entry.path,
                )
            )
        undeclared = set(static_effects) - set(declared_effects)
        if undeclared:
            diagnostics.append(
                PromotionDiagnostic(
                    code="undeclared_static_effect",
                    severity="blocker",
                    message="source implies undeclared effects: "
                    + ", ".join(sorted(undeclared)),
                )
            )
        missing_bounds = REQUIRED_R0_BOUNDS - set(metadata["bounds"])
        if missing_bounds:
            diagnostics.append(
                PromotionDiagnostic(
                    code="unenforced_resource_bounds",
                    severity="blocker",
                    message="missing enforced bounds: "
                    + ", ".join(sorted(missing_bounds)),
                )
            )
        closure_hashes = {
            path: sha256_bytes(raw) for path, raw in sorted(files.items())
        }
        closure_id = _closure_id(request.entry, closure_hashes)
        content_id = _content_id(request.entry, closure_id)
        if request.local_run is None or not request.local_run.succeeded:
            diagnostics.append(
                PromotionDiagnostic(
                    code="local_run_evidence_missing",
                    severity="blocker",
                    message="a successful local run bound to this snapshot is required",
                )
            )
        elif request.local_run.closure_id != closure_id:
            diagnostics.append(
                PromotionDiagnostic(
                    code="local_run_evidence_mismatch",
                    severity="blocker",
                    message="local run evidence is for a different forward closure",
                )
            )
        risk = (
            "R0"
            if computed_effects
            and all(_is_r0_effect(effect) for effect in computed_effects)
            and not any(item.severity == "blocker" for item in diagnostics)
            else "R2"
        )
        closure = [
            PromotionClosureMember(
                path=path,
                sha256=request.snapshot.files[path],
                size=len(raw),
                relation="selected" if path == request.entry.path else "dependency",
            )
            for path, raw in sorted(files.items())
        ]
        effective_files = dict(sorted({**base.hashes, **closure_hashes}.items()))
        effective_manifest_id = _manifest_id(effective_files)
        registration_key = f"{request.entry.path}::{request.entry.function}"
        action = registration_intent[0] if registration_intent else {}
        effective_registration = {
            "path": request.entry.path,
            "function": request.entry.function,
            "workflow_id": (
                action.get("requested_id")
                or (registration_state or {}).get("workflow_id")
            ),
            "type": action.get("type", "workflow"),
            "name": action.get("name", request.entry.function),
            "organization_id": action.get("organization_id"),
            "is_active": True,
            "source_sha256": closure_hashes[request.entry.path],
        }
        effective_registrations = dict(sorted({
            **base.registrations,
            registration_key: effective_registration,
        }.items()))
        effective_registration_manifest_id = workspace_registration_manifest_id(
            effective_registrations
        )
        release_payload = {
            "organization_id": str(self.organization_id),
            "base_release_id": base.release_id,
            "base_manifest_id": base.manifest_id,
            "effective_manifest_id": effective_manifest_id,
            "effective_files": effective_files,
            "effective_registration_manifest_id": (
                effective_registration_manifest_id
            ),
            "effective_registrations": effective_registrations,
            "entry": request.entry.model_dump(),
            "registration_intent_fingerprint": registration_intent_fingerprint,
            "protected_source": protected_source.model_dump(),
        }
        release_id = workspace_release_id(release_payload)
        candidate_payload = {
            "schema_version": PROMOTION_ARTIFACT_SCHEMA,
            "organization_id": str(self.organization_id),
            "target": request.target,
            "entry": request.entry.model_dump(),
            "content_id": content_id,
            "closure_id": closure_id,
            "release_id": release_id,
            "base_release_id": base.release_id,
            "base_manifest_id": base.manifest_id,
            "effective_manifest_id": effective_manifest_id,
            "effective_files": effective_files,
            "effective_registration_manifest_id": (
                effective_registration_manifest_id
            ),
            "effective_registrations": effective_registrations,
            "snapshot_id": request.snapshot.snapshot_id,
            "closure": [item.model_dump() for item in closure],
            "protected_source": protected_source.model_dump(),
            "declared_effects": declared_effects,
            "static_effects": static_effects,
            "computed_effects": computed_effects,
            "bounds": metadata["bounds"],
            "requested_bounds": metadata["requested_bounds"],
            "diagnostics": [item.model_dump() for item in diagnostics],
            "registration": {
                "intent": registration_intent,
                "intent_fingerprint": registration_intent_fingerprint,
                "state": registration_state,
                "state_fingerprint": registration_state_fingerprint,
            },
            "local_run": request.local_run.model_dump(mode="json")
            if request.local_run
            else None,
            "client": request.client.model_dump(),
            "policy_version": PROMOTION_PREVIEW_POLICY,
            "supersedes_candidate_id": request.supersedes_candidate_id,
        }
        candidate_id = _canonical_candidate(candidate_payload)
        expires_at = datetime.now(timezone.utc) + ARTIFACT_TTL
        await self._lock_artifact_creation()
        artifact = await self._find_artifact(candidate_id)
        if artifact is None:
            superseded = await self._resolve_superseded(
                request.supersedes_candidate_id
            )
            storage = WorkspacePromotionArtifactStorage(
                self.organization_id, content_id
            )
            manifest_bytes = json.dumps(
                {
                    "schema": WORKSPACE_RELEASE_CONTENT_SCHEMA,
                    "content_id": content_id,
                    "closure_id": closure_id,
                    "entry": request.entry.model_dump(),
                    "files": closure_hashes,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            source_key = await storage.write_source(_source_zip(files))
            manifest_key = await storage.write_manifest(manifest_bytes)
            artifact = WorkspacePromotionArtifact(
                organization_id=self.organization_id,
                candidate_id=candidate_id,
                content_id=content_id,
                closure_id=closure_id,
                release_id=release_id,
                base_release_id=base.release_id,
                base_manifest_id=base.manifest_id,
                effective_manifest_id=effective_manifest_id,
                effective_registration_manifest_id=(
                    effective_registration_manifest_id
                ),
                registration_intent_fingerprint=registration_intent_fingerprint,
                registration_state_fingerprint=registration_state_fingerprint,
                schema_version=PROMOTION_BUNDLE_SCHEMA_V2,
                target_kind="workspace",
                entity_type="workflow",
                entry_path=request.entry.path,
                entry_function=request.entry.function,
                snapshot_id=request.snapshot.snapshot_id,
                source_revision=protected_source.commit_sha,
                source_tree_sha=protected_source.tree_sha,
                source_artifact_key=source_key,
                manifest_key=manifest_key,
                manifest=candidate_payload,
                risk_class=risk,
                disposition="review_required",
                artifact_state="review_required",
                policy_version=PROMOTION_PREVIEW_POLICY,
                created_by=user_id,
                expires_at=expires_at,
                supersedes_artifact_id=superseded.id if superseded else None,
            )
            self.db.add(artifact)
            await self.db.flush()
        await emit_audit(
            self.db,
            "workspace_promotion.preview",
            resource_type="workspace_promotion_artifact",
            resource_id=artifact.id,
            details={
                "candidate_id": candidate_id,
                "content_id": content_id,
                "release_id": release_id,
                "snapshot_id": request.snapshot.snapshot_id,
                "entry_path": request.entry.path,
                "entry_function": request.entry.function,
                "risk_class": risk,
                "policy_version": PROMOTION_PREVIEW_POLICY,
                "closure": [
                    {"path": item.path, "sha256": item.sha256} for item in closure
                ],
            },
            strict=True,
        )
        await self.db.commit()
        await self.db.refresh(artifact)
        registration = PromotionRegistrationEvidence(
            intent=registration_intent,
            intent_fingerprint=registration_intent_fingerprint,
            state=registration_state,
            state_fingerprint=registration_state_fingerprint,
        )
        lifecycle = await self._lifecycle(artifact)
        return WorkspacePromotionPreviewResponse(
            ready_to_activate=False,
            disposition="review_required",
            artifact_id=artifact.id,
            candidate_id=candidate_id,
            content_id=content_id,
            closure_id=closure_id,
            release_id=release_id,
            base_release_id=base.release_id,
            base_manifest_id=base.manifest_id,
            effective_manifest_id=effective_manifest_id,
            effective_files=effective_files,
            effective_registration_manifest_id=(
                effective_registration_manifest_id
            ),
            effective_registrations=effective_registrations,
            snapshot_id=request.snapshot.snapshot_id,
            risk_class=risk,
            policy_version=PROMOTION_PREVIEW_POLICY,
            closure=closure,
            declared_effects=declared_effects,
            static_effects=static_effects,
            computed_effects=computed_effects,
            bounds=metadata["bounds"],
            requested_bounds=metadata["requested_bounds"],
            registration=registration,
            protected_source=protected_source,
            lifecycle_status=lifecycle,
            supersedes_candidate_id=request.supersedes_candidate_id,
            source_artifact_key=artifact.source_artifact_key,
            diagnostics=diagnostics,
            expires_at=artifact.expires_at,
        )

    async def _existing_workflow(self, path: str, function: str) -> Workflow | None:
        return await find_workspace_workflow(
            self.db,
            self.organization_id,
            path,
            function,
        )

    async def _read_protected_source(
        self, request: WorkspacePromotionPreviewRequest
    ) -> tuple[PromotionSourceEvidence, dict[str, bytes]]:
        if self.commit_writer is None:
            raise WorkspacePromotionInvalid(
                "reviewed production preview requires the protected Git source reader"
            )
        paths = tuple(
            sorted(
                normalize_workspace_path(item.path)
                for item in request.snapshot.closure
            )
        )
        if any(
            normalize_workspace_path(item.path) != item.path
            for item in request.snapshot.closure
        ):
            raise WorkspacePromotionInvalid(
                "closure paths must use canonical POSIX form"
            )
        try:
            source = await self.commit_writer.read_files(paths, ref="main")
        except PlatformCommitError as exc:
            raise WorkspacePromotionInvalid(str(exc)) from exc
        if source.commit_sha != request.protected_source.commit_sha:
            raise WorkspacePromotionInvalid(
                "protected main advanced after local review; fetch origin/main and "
                "rebuild the promotion preview from the current protected head"
            )
        if source.tree_sha != request.protected_source.tree_sha:
            raise WorkspacePromotionInvalid(
                "protected-main tree SHA changed after local review"
            )
        return (
            PromotionSourceEvidence(
                commit_sha=source.commit_sha,
                tree_sha=source.tree_sha,
            ),
            source.files,
        )

    async def _resolve_base(
        self, request: WorkspacePromotionPreviewRequest
    ) -> _BaseSnapshot:
        base = await self._active_or_repo_base()
        expected = request.expected_base_release_id
        if expected is not None and expected != base.release_id:
            raise WorkspacePromotionInvalid(
                "active Workspace base changed after the caller selected it"
            )
        return base

    async def _active_or_repo_base(self) -> _BaseSnapshot:
        if self.base_resolver is not None:
            return await self.base_resolver()
        rows = (
            await self.db.execute(
                select(WorkspacePromotionRelease, WorkspacePromotionArtifact)
                .join(
                    WorkspacePromotionArtifact,
                    WorkspacePromotionArtifact.id
                    == WorkspacePromotionRelease.artifact_id,
                )
                .where(
                    WorkspacePromotionRelease.organization_id == self.organization_id,
                    WorkspacePromotionRelease.activation_state == "live",
                )
                .limit(2)
            )
        ).all()
        if len(rows) > 1:
            raise WorkspacePromotionInvalid(
                "organization has more than one active Workspace release"
            )
        if not rows:
            return await self._repo_v1_base()
        from src.services.workspace_release_runtime import (
            WorkspaceReleaseDescriptor,
        )
        from src.services.workspace_release_storage import WorkspaceReleaseStorage

        release, artifact = rows[0]
        try:
            descriptor = WorkspaceReleaseDescriptor.from_rows(release, artifact)
            files = await WorkspaceReleaseStorage(
                descriptor.runtime_storage_prefix
            ).read_many(sorted(descriptor.source_hashes))
        except Exception as exc:  # noqa: BLE001 - storage backends vary
            raise WorkspacePromotionInvalid(
                "active Workspace release base is not readable"
            ) from exc
        hashes = {path: sha256_bytes(raw) for path, raw in sorted(files.items())}
        if (
            hashes != descriptor.source_hashes
            or _manifest_id(hashes) != descriptor.effective_manifest_id
        ):
            raise WorkspacePromotionInvalid(
                "active Workspace release base failed immutable readback"
            )
        return _BaseSnapshot(
            release_id=descriptor.release_id,
            manifest_id=descriptor.effective_manifest_id,
            files=files,
            hashes=hashes,
            registrations=descriptor.effective_registrations,
        )

    async def _repo_v1_base(self) -> _BaseSnapshot:
        before = await self.workspace_generation()
        if before.startswith("updating:"):
            raise WorkspacePromotionInvalid(
                "workspace source update is in progress; retry preview"
            )
        paths = sorted(
            path
            for path in await self.repo_storage.list()
            if _is_executable_python_path(path)
        )
        if len(paths) > MAX_SNAPSHOT_FILES:
            raise WorkspacePromotionInvalid(
                f"workspace base exceeds {MAX_SNAPSHOT_FILES} files"
            )
        reject_path_collisions(paths)
        read_many = getattr(self.repo_storage, "read_many", None)
        if read_many is not None:
            files = await read_many(paths, concurrency=32)
        else:
            semaphore = asyncio.Semaphore(32)

            async def read(path: str) -> tuple[str, bytes]:
                async with semaphore:
                    return path, await self.repo_storage.read(path)

            files = dict(await asyncio.gather(*(read(path) for path in paths)))
        after = await self.workspace_generation()
        if before != after or after.startswith("updating:"):
            raise WorkspacePromotionInvalid(
                "workspace source changed while resolving the release base"
            )
        hashes = {path: sha256_bytes(raw) for path, raw in sorted(files.items())}
        return _BaseSnapshot(
            release_id=_repo_v1_release_id(hashes),
            manifest_id=_manifest_id(hashes),
            files=files,
            hashes=hashes,
            registrations={},
        )

    async def _resolve_superseded(
        self, candidate_id: str | None
    ) -> WorkspacePromotionArtifact | None:
        if candidate_id is None:
            return None
        artifact = await self._find_artifact(candidate_id)
        if artifact is None:
            raise WorkspacePromotionInvalid(
                "superseded candidate does not exist in this organization"
            )
        if artifact.target_kind != "workspace":
            raise WorkspacePromotionInvalid(
                "local draft artifacts cannot be superseded by reviewed releases"
            )
        if artifact.expires_at <= datetime.now(timezone.utc):
            raise WorkspacePromotionInvalid("expired candidates cannot be superseded")
        if artifact.artifact_state == "invalid":
            raise WorkspacePromotionInvalid("invalid candidates cannot be superseded")
        result = await self.db.execute(
            select(WorkspacePromotionArtifact.id).where(
                WorkspacePromotionArtifact.organization_id == self.organization_id,
                WorkspacePromotionArtifact.supersedes_artifact_id == artifact.id,
            )
        )
        if result.scalar_one_or_none() is not None:
            raise WorkspacePromotionInvalid("candidate is already superseded")
        return artifact

    async def _lifecycle(self, artifact: WorkspacePromotionArtifact) -> str:
        result = await self.db.execute(
            select(WorkspacePromotionArtifact.id).where(
                WorkspacePromotionArtifact.organization_id == self.organization_id,
                WorkspacePromotionArtifact.supersedes_artifact_id == artifact.id,
            )
        )
        if result.scalar_one_or_none() is not None:
            return "superseded"
        if artifact.expires_at <= datetime.now(timezone.utc):
            return "expired"
        return artifact.artifact_state

    async def get_artifact(
        self, artifact_id: UUID
    ) -> WorkspacePromotionArtifactResponse:
        result = await self.db.execute(
            select(WorkspacePromotionArtifact).where(
                WorkspacePromotionArtifact.id == artifact_id,
                WorkspacePromotionArtifact.organization_id == self.organization_id,
            )
        )
        artifact = result.scalar_one_or_none()
        if artifact is None or artifact.schema_version != PROMOTION_BUNDLE_SCHEMA_V2:
            raise KeyError(artifact_id)
        manifest = artifact.manifest
        return WorkspacePromotionArtifactResponse(
            artifact_id=artifact.id,
            candidate_id=artifact.candidate_id,
            content_id=str(artifact.content_id),
            closure_id=str(artifact.closure_id),
            release_id=str(artifact.release_id),
            base_release_id=str(artifact.base_release_id),
            base_manifest_id=str(artifact.base_manifest_id),
            effective_manifest_id=str(artifact.effective_manifest_id),
            effective_files=manifest["effective_files"],
            effective_registration_manifest_id=str(
                artifact.effective_registration_manifest_id
            ),
            effective_registrations=manifest["effective_registrations"],
            entry=manifest["entry"],
            closure=manifest["closure"],
            registration=manifest["registration"],
            protected_source=manifest["protected_source"],
            declared_effects=manifest["declared_effects"],
            computed_effects=manifest["computed_effects"],
            bounds=manifest["bounds"],
            local_run=manifest.get("local_run"),
            diagnostics=manifest.get("diagnostics", []),
            lifecycle_status=await self._lifecycle(artifact),
            supersedes_candidate_id=manifest.get("supersedes_candidate_id"),
            source_artifact_key=artifact.source_artifact_key,
            expires_at=artifact.expires_at,
            created_at=artifact.created_at,
        )

    async def _find_artifact(
        self, candidate_id: str
    ) -> WorkspacePromotionArtifact | None:
        result = await self.db.execute(
            select(WorkspacePromotionArtifact).where(
                WorkspacePromotionArtifact.organization_id == self.organization_id,
                WorkspacePromotionArtifact.candidate_id == candidate_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _activation_state(existing: Workflow | None) -> dict[str, Any] | None:
        if existing is None:
            return None
        return {
            "workflow_id": str(existing.id),
            "organization_id": str(existing.organization_id)
            if existing.organization_id
            else None,
            "type": existing.type,
            "is_active": existing.is_active,
            "endpoint_enabled": existing.endpoint_enabled,
            "public_endpoint": existing.public_endpoint,
            "api_key_enabled": existing.api_key_enabled,
            "access_level": existing.access_level,
            "role_ids": sorted(str(role.id) for role in existing.roles),
        }
