"""Preview-only compiler for immutable rapid Workspace promotion artifacts."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bifrost.promotion import (
    MAX_SNAPSHOT_FILES,
    PROMOTION_BUNDLE_SCHEMA,
    dependency_edges,
    sha256_bytes,
    validate_submitted_bundle,
)
from src.models.contracts.workspace_promotions import (
    PromotionClosureMember,
    PromotionDiagnostic,
    WorkspacePromotionPreviewRequest,
    WorkspacePromotionPreviewResponse,
)
from src.models.orm.workspace_promotions import WorkspacePromotionArtifact
from src.models.orm.workflows import Workflow
from src.services.repo_storage import RepoStorage
from src.services.audit import emit_audit
from src.services.workflow_registration import (
    WorkspaceRegistrationCandidate,
    plan_workspace_registrations,
)
from src.services.workspace_promotion_storage import WorkspacePromotionArtifactStorage

PROMOTION_PREVIEW_POLICY = "workspace-rapid-preview/2026-08-14"
ARTIFACT_TTL = timedelta(days=7)
R0_EFFECTS = {"bifrost.read", "integration.read"}
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


class WorkspacePromotionPreviewService:
    """Build and persist immutable previews; intentionally cannot activate them."""

    def __init__(
        self,
        db: AsyncSession,
        organization_id: UUID,
        *,
        repo_storage: RepoStorage | None = None,
    ):
        self.db = db
        self.organization_id = organization_id
        self.repo_storage = repo_storage or RepoStorage()

    async def preview(
        self, request: WorkspacePromotionPreviewRequest, user_id: UUID
    ) -> WorkspacePromotionPreviewResponse:
        try:
            files = validate_submitted_bundle(
                selected_path=request.entry.path,
                snapshot_id_value=request.snapshot.snapshot_id,
                snapshot_files=request.snapshot.files,
                files=[item.model_dump() for item in request.snapshot.closure],
            )
            metadata = _entry_metadata(
                files[request.entry.path], request.entry.path, request.entry.function
            )
        except (ValueError, KeyError) as exc:
            raise WorkspacePromotionInvalid(str(exc)) from exc

        static_effects, diagnostics = _static_effects(files)
        diagnostics.extend(await self._reverse_dependency_diagnostics(files))
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
        registration = action_list[0] if action_list else {}
        existing = await self._existing_workflow(
            request.entry.path, request.entry.function
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
                        "existing global, tool/provider, endpoint, API-key, or non-role-based "
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
        if request.local_run is None or not request.local_run.succeeded:
            diagnostics.append(
                PromotionDiagnostic(
                    code="local_run_evidence_missing",
                    severity="blocker",
                    message="a successful local run bound to this snapshot is required",
                )
            )
        diagnostics.extend(
            [
                PromotionDiagnostic(
                    code="server_run_evidence_unavailable",
                    severity="blocker",
                    message="local evidence is not yet server-issued and verifiable",
                ),
                PromotionDiagnostic(
                    code="production_loader_smoke_unavailable",
                    severity="blocker",
                    message="candidate-backed production loader smoke is not implemented yet",
                ),
                PromotionDiagnostic(
                    code="preview_only_dark_launch",
                    severity="blocker",
                    message="rapid promotion activation is intentionally disabled",
                ),
            ]
        )
        r2_diagnostic_codes = {
            "existing_activation_surface",
            "local_run_evidence_missing",
            "registration_conflict",
            "reverse_dependency_scan_failed",
            "shared_dependency_outside_candidate",
            "undeclared_static_effect",
            "unenforced_resource_bounds",
        }
        risk = (
            "R0"
            if computed_effects
            and all(_is_r0_effect(effect) for effect in computed_effects)
            and not any(item.code in r2_diagnostic_codes for item in diagnostics)
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
        candidate_payload = {
            "schema_version": PROMOTION_BUNDLE_SCHEMA,
            "organization_id": str(self.organization_id),
            "target": request.target,
            "entry": request.entry.model_dump(),
            "snapshot_id": request.snapshot.snapshot_id,
            "closure": [item.model_dump() for item in closure],
            "source_revision": request.source_revision,
            "declared_effects": declared_effects,
            "static_effects": static_effects,
            "computed_effects": computed_effects,
            "bounds": metadata["bounds"],
            "requested_bounds": metadata["requested_bounds"],
            "registration": registration,
            "existing_activation": self._activation_state(existing),
            "local_run": request.local_run.model_dump(mode="json")
            if request.local_run
            else None,
            "client": request.client.model_dump(),
            "policy_version": PROMOTION_PREVIEW_POLICY,
        }
        candidate_id = _canonical_candidate(candidate_payload)
        expires_at = datetime.now(timezone.utc) + ARTIFACT_TTL
        artifact = await self._find_artifact(candidate_id)
        if artifact is None:
            storage = WorkspacePromotionArtifactStorage(
                self.organization_id, candidate_id
            )
            manifest_bytes = json.dumps(
                candidate_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            source_key = await storage.write_source(_source_zip(files))
            manifest_key = await storage.write_manifest(manifest_bytes)
            artifact = WorkspacePromotionArtifact(
                organization_id=self.organization_id,
                candidate_id=candidate_id,
                schema_version=PROMOTION_BUNDLE_SCHEMA,
                target_kind="workspace",
                entity_type="workflow",
                entry_path=request.entry.path,
                entry_function=request.entry.function,
                snapshot_id=request.snapshot.snapshot_id,
                source_revision=request.source_revision,
                source_artifact_key=source_key,
                manifest_key=manifest_key,
                manifest=candidate_payload,
                risk_class=risk,
                disposition="review_required",
                artifact_state="review_required",
                policy_version=PROMOTION_PREVIEW_POLICY,
                created_by=user_id,
                expires_at=expires_at,
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
        return WorkspacePromotionPreviewResponse(
            disposition="review_required",
            artifact_id=artifact.id,
            candidate_id=candidate_id,
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
            diagnostics=diagnostics,
            expires_at=artifact.expires_at,
        )

    async def _existing_workflow(self, path: str, function: str) -> Workflow | None:
        result = await self.db.execute(
            select(Workflow)
            .where(
                Workflow.path == path,
                Workflow.function_name == function,
                Workflow.solution_id.is_(None),
                or_(
                    Workflow.organization_id == self.organization_id,
                    Workflow.organization_id.is_(None),
                ),
            )
            .options(selectinload(Workflow.roles))
        )
        return result.scalar_one_or_none()

    async def _reverse_dependency_diagnostics(
        self, candidate_files: dict[str, bytes]
    ) -> list[PromotionDiagnostic]:
        """Fail closed when a changed path has an outside live importer."""
        try:
            live_paths = sorted(
                path for path in await self.repo_storage.list() if path.endswith(".py")
            )
            if len(live_paths) > MAX_SNAPSHOT_FILES:
                raise WorkspacePromotionInvalid(
                    "live workspace is too large for reverse-dependency validation"
                )
            semaphore = asyncio.Semaphore(32)

            async def read(path: str) -> tuple[str, bytes]:
                async with semaphore:
                    return path, await self.repo_storage.read(path)

            live_files = dict(
                await asyncio.gather(*(read(path) for path in live_paths))
            )
            graph_files = {**live_files, **candidate_files}
            edges = dependency_edges(graph_files)
        except WorkspacePromotionInvalid:
            raise
        except Exception as exc:  # noqa: BLE001 - storage backends differ
            return [
                PromotionDiagnostic(
                    code="reverse_dependency_scan_failed",
                    severity="blocker",
                    message=f"could not prove live reverse dependencies: {type(exc).__name__}",
                )
            ]

        closure = set(candidate_files)
        changed = {
            path
            for path, raw in candidate_files.items()
            if path not in live_files
            or sha256_bytes(live_files[path]) != sha256_bytes(raw)
        }
        diagnostics: list[PromotionDiagnostic] = []
        for changed_path in sorted(changed):
            outside_importers = sorted(
                importer
                for importer, dependencies in edges.items()
                if changed_path in dependencies and importer not in closure
            )
            if outside_importers:
                diagnostics.append(
                    PromotionDiagnostic(
                        code="shared_dependency_outside_candidate",
                        severity="blocker",
                        message=(
                            "changed source has live reverse consumers outside the candidate: "
                            + ", ".join(outside_importers[:20])
                        ),
                        path=changed_path,
                    )
                )
        return diagnostics

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
