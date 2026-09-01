"""Durable-storage impact graph for guarded Workspace Python edits."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from opentelemetry import metrics

from bifrost.promotion import (
    MAX_SNAPSHOT_FILES,
    PromotionBundleError,
    normalize_workspace_path,
    snapshot_id,
)
from bifrost.workspace_impact import (
    WORKSPACE_IMPORT_ROOTS,
    WorkspaceImpactAnalysis,
    WorkspaceImpactIndex,
    analyze_workspace_impact,
    index_workspace_impact,
    reverse_edges,
    transitive_distances,
)
from src.models.contracts.workspace_file_impact import (
    IMPACT_SCHEMA,
    WorkspaceFileImpactDiagnostic,
    WorkspaceFileImpactEdge,
    WorkspaceFileImpactMember,
    WorkspaceFileImpactRequest,
    WorkspaceFileImpactResponse,
)
from src.services.repo_storage import RepoStorage

LARGE_IMPACT_GRAPH_FILES = 200
logger = logging.getLogger(__name__)
workspace_impact_snapshot_duration = None
workspace_impact_snapshot_files = None
workspace_impact_barrier_duration = None


@dataclass(frozen=True)
class _WorkspaceSnapshot:
    files: dict[str, bytes]
    hashes: dict[str, str]
    snapshot_id: str
    impact_index: WorkspaceImpactIndex


class _WorkspaceSnapshotCache:
    """Single-flight durable snapshots scoped to one workspace generation."""

    def __init__(self) -> None:
        self._generation: str | None = None
        self._snapshot: _WorkspaceSnapshot | None = None
        self._lock = asyncio.Lock()

    async def get(
        self,
        *,
        generation: Callable[[], Awaitable[str]],
        load: Callable[[], Awaitable[_WorkspaceSnapshot]],
    ) -> _WorkspaceSnapshot:
        async with self._lock:
            before = await generation()
            if before.startswith("updating:"):
                raise WorkspaceFileImpactInvalid(
                    "workspace source update is in progress; retry impact analysis"
                )
            if before == self._generation and self._snapshot is not None:
                return self._snapshot

            snapshot = await load()
            after = await generation()
            if after != before or after.startswith("updating:"):
                raise WorkspaceFileImpactInvalid(
                    "workspace source changed during impact analysis; retry"
                )
            self._generation = after
            self._snapshot = snapshot
            return snapshot


_workspace_snapshot_cache = _WorkspaceSnapshotCache()


def _is_authored_python_path(path: str) -> bool:
    return path.endswith(".py") and path.split("/", 1)[0] in WORKSPACE_IMPORT_ROOTS


class WorkspaceFileImpactInvalid(ValueError):
    """The requested graph cannot be proven safely."""


class WorkspaceFileImpactConflict(RuntimeError):
    """The durable graph no longer matches the reviewed candidate."""

    def __init__(self, path: str, expected: str, current: str):
        super().__init__("Workspace dependency state changed after impact preview.")
        self.path = path
        self.expected = expected
        self.current = current


class WorkspaceFileImpactBlocked(RuntimeError):
    """The recomputed graph contains blocking diagnostics."""

    def __init__(self, response: WorkspaceFileImpactResponse):
        super().__init__("Workspace dependency impact could not be proven safely.")
        self.response = response


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _candidate_id(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return f"sha256:{_sha256(canonical)}"


def _impact_metric_instruments():
    """Create instruments after the process MeterProvider is configured."""
    global workspace_impact_barrier_duration
    global workspace_impact_snapshot_duration
    global workspace_impact_snapshot_files

    meter = metrics.get_meter(__name__)
    if workspace_impact_snapshot_duration is None:
        workspace_impact_snapshot_duration = meter.create_histogram(
            "bifrost.workspace.impact.snapshot.duration",
            unit="ms",
            description="Duration of durable Workspace Python inventory snapshots.",
        )
    if workspace_impact_snapshot_files is None:
        workspace_impact_snapshot_files = meter.create_histogram(
            "bifrost.workspace.impact.snapshot.files",
            unit="{file}",
            description="Number of files in a Workspace impact snapshot.",
        )
    if workspace_impact_barrier_duration is None:
        workspace_impact_barrier_duration = meter.create_histogram(
            "bifrost.workspace.impact.writer_barrier.duration",
            unit="ms",
            description="Duration of checked writes under the source writer barrier.",
        )
    return (
        workspace_impact_snapshot_duration,
        workspace_impact_snapshot_files,
        workspace_impact_barrier_duration,
    )


def _diagnostic_severity(
    *,
    path: str,
    changed: bool,
    forward_paths: set[str],
    affected_path: str,
    newly_introduced: bool,
    registry_edge_to_target: bool = False,
) -> Literal["warning", "blocker"]:
    if not changed:
        return "warning"
    if (
        affected_path == path
        or affected_path in forward_paths
        or newly_introduced
        or registry_edge_to_target
    ):
        return "blocker"
    return "warning"


def _preexisting_prefix(
    *,
    path: str,
    affected_path: str,
    severity: Literal["warning", "blocker"],
) -> str:
    if severity != "warning" or affected_path == path:
        return ""
    return "pre-existing connected-file issue: "


def _diagnostics(
    *,
    path: str,
    changed: bool,
    relevant_paths: set[str],
    forward_paths: set[str],
    reverse_paths: set[str],
    analysis: WorkspaceImpactAnalysis,
    base_analysis: WorkspaceImpactAnalysis,
    proposed_unchanged: bool,
) -> list[WorkspaceFileImpactDiagnostic]:
    diagnostics: list[WorkspaceFileImpactDiagnostic] = []
    if len(relevant_paths) > LARGE_IMPACT_GRAPH_FILES:
        diagnostics.append(
            WorkspaceFileImpactDiagnostic(
                code="large_impact_graph",
                severity="info",
                message=(
                    f"complete transitive traversal analyzed all "
                    f"{len(relevant_paths)} files without truncation"
                ),
                path=path,
            )
        )

    for affected_path in sorted(relevant_paths):
        ambiguous = analysis.ambiguous_references.get(affected_path, ())
        if ambiguous:
            base_ambiguous = set(
                base_analysis.ambiguous_references.get(affected_path, ())
            )
            severity = _diagnostic_severity(
                path=path,
                changed=changed,
                forward_paths=forward_paths,
                affected_path=affected_path,
                newly_introduced=not set(ambiguous).issubset(base_ambiguous),
                registry_edge_to_target=(
                    (affected_path, path) in analysis.registry_edges
                ),
            )
            diagnostics.append(
                WorkspaceFileImpactDiagnostic(
                    code="ambiguous_workflow_reference",
                    severity=severity,
                    message=(
                        _preexisting_prefix(
                            path=path,
                            affected_path=affected_path,
                            severity=severity,
                        )
                        + "workflow reference resolves to multiple authored files: "
                        + ", ".join(ambiguous)
                    ),
                    path=affected_path,
                )
            )
        unresolved = analysis.unresolved_imports.get(affected_path, ())
        if unresolved:
            base_unresolved = set(
                base_analysis.unresolved_imports.get(affected_path, ())
            )
            severity = _diagnostic_severity(
                path=path,
                changed=changed,
                forward_paths=forward_paths,
                affected_path=affected_path,
                newly_introduced=not set(unresolved).issubset(base_unresolved),
            )
            diagnostics.append(
                WorkspaceFileImpactDiagnostic(
                    code="unresolved_repo_import",
                    severity=severity,
                    message=(
                        _preexisting_prefix(
                            path=path,
                            affected_path=affected_path,
                            severity=severity,
                        )
                        + "unresolved repo-local imports: "
                        + ", ".join(unresolved)
                    ),
                    path=affected_path,
                )
            )
        if affected_path in analysis.dynamic_importers:
            severity = _diagnostic_severity(
                path=path,
                changed=changed,
                forward_paths=forward_paths,
                affected_path=affected_path,
                newly_introduced=(affected_path not in base_analysis.dynamic_importers),
            )
            diagnostics.append(
                WorkspaceFileImpactDiagnostic(
                    code="dynamic_import_unresolved",
                    severity=severity,
                    message=(
                        _preexisting_prefix(
                            path=path,
                            affected_path=affected_path,
                            severity=severity,
                        )
                        + "computed dynamic import prevents complete static impact proof"
                    ),
                    path=affected_path,
                )
            )
        if affected_path in analysis.dynamic_reference_importers:
            severity = _diagnostic_severity(
                path=path,
                changed=changed,
                forward_paths=forward_paths,
                affected_path=affected_path,
                newly_introduced=(
                    affected_path not in base_analysis.dynamic_reference_importers
                ),
            )
            diagnostics.append(
                WorkspaceFileImpactDiagnostic(
                    code="dynamic_workflow_reference_unresolved",
                    severity=severity,
                    message=(
                        _preexisting_prefix(
                            path=path,
                            affected_path=affected_path,
                            severity=severity,
                        )
                        + "computed workflow reference prevents complete static impact proof"
                    ),
                    path=affected_path,
                )
            )
    if reverse_paths:
        diagnostics.append(
            WorkspaceFileImpactDiagnostic(
                code="reverse_dependents_present",
                severity="info",
                message=(
                    f"{len(reverse_paths)} transitive reverse dependent(s) require validation"
                ),
                path=path,
            )
        )
    if proposed_unchanged:
        diagnostics.append(
            WorkspaceFileImpactDiagnostic(
                code="proposed_bytes_unchanged",
                severity="warning",
                message="proposed bytes already match durable workspace storage",
                path=path,
            )
        )
    return diagnostics


def _importer_symbol_contract_diagnostics(
    *,
    path: str,
    importer_path: str,
    removed: set[str],
    current_symbols: frozenset[str],
    dynamic_contract_changed: bool,
    analysis: WorkspaceImpactAnalysis,
    base_analysis: WorkspaceImpactAnalysis,
) -> list[WorkspaceFileImpactDiagnostic]:
    diagnostics: list[WorkspaceFileImpactDiagnostic] = []
    required = set(analysis.symbol_imports.get((importer_path, path), ()))
    removed_required = sorted(removed & required)
    dynamic_required = sorted(required - current_symbols)
    if dynamic_contract_changed and dynamic_required:
        diagnostics.append(
            WorkspaceFileImpactDiagnostic(
                code="dynamic_module_export_contract",
                severity="blocker",
                message=(
                    "proposed source changes the dynamic module export contract "
                    "used for symbols: "
                )
                + ", ".join(dynamic_required),
                path=importer_path,
            )
        )
    if removed_required:
        dynamic = (
            path in base_analysis.dynamic_exporters
            or path in analysis.dynamic_exporters
        )
        diagnostics.append(
            WorkspaceFileImpactDiagnostic(
                code=(
                    "dynamic_module_export_contract"
                    if dynamic
                    else "removed_imported_symbol"
                ),
                severity="blocker",
                message=(
                    "module uses dynamic exports; cannot prove removed symbols: "
                    if dynamic
                    else "proposed source removes imported symbols: "
                )
                + ", ".join(removed_required),
                path=importer_path,
            )
        )
    if (importer_path, path) in analysis.star_import_edges:
        cause = (
            "removes module-level symbols"
            if removed
            else "changes the dynamic module export contract"
        )
        diagnostics.append(
            WorkspaceFileImpactDiagnostic(
                code="star_import_contract_unresolved",
                severity="blocker",
                message=("consumer uses a star import while the dependency " + cause),
                path=importer_path,
            )
        )
    return diagnostics


def _symbol_contract_diagnostics(
    *,
    path: str,
    current_exists: bool,
    changed: bool,
    reverse_paths: set[str],
    analysis: WorkspaceImpactAnalysis,
    base_analysis: WorkspaceImpactAnalysis,
) -> list[WorkspaceFileImpactDiagnostic]:
    if not current_exists or not changed:
        return []
    current_symbols = base_analysis.module_symbols.get(path, frozenset())
    proposed_symbols = analysis.module_symbols.get(path, frozenset())
    removed = set(current_symbols - proposed_symbols)
    dynamic_contract_changed = base_analysis.dynamic_export_contracts.get(
        path, ()
    ) != analysis.dynamic_export_contracts.get(path, ())
    if not removed and not dynamic_contract_changed:
        return []

    diagnostics: list[WorkspaceFileImpactDiagnostic] = []
    for importer_path in sorted(reverse_paths):
        diagnostics.extend(
            _importer_symbol_contract_diagnostics(
                path=path,
                importer_path=importer_path,
                removed=removed,
                current_symbols=current_symbols,
                dynamic_contract_changed=dynamic_contract_changed,
                analysis=analysis,
                base_analysis=base_analysis,
            )
        )
    return diagnostics


def _members(
    paths: set[str], distances: Mapping[str, int], hashes: Mapping[str, str]
) -> list[WorkspaceFileImpactMember]:
    return [
        WorkspaceFileImpactMember(
            path=item_path,
            sha256=hashes[item_path],
            depth=distances[item_path],
        )
        for item_path in sorted(paths, key=lambda value: (distances[value], value))
    ]


def _edge_rows(
    *, analysis: WorkspaceImpactAnalysis, relevant_paths: set[str]
) -> list[WorkspaceFileImpactEdge]:
    rows: list[WorkspaceFileImpactEdge] = []
    for importer in sorted(relevant_paths):
        for dependency in sorted(analysis.edges.get(importer, ())):
            if dependency not in relevant_paths:
                continue
            kind = (
                "registry"
                if (importer, dependency) in analysis.registry_edges
                and (importer, dependency) not in analysis.import_edges
                else "import"
            )
            rows.append(
                WorkspaceFileImpactEdge(
                    importer=importer,
                    dependency=dependency,
                    kind=kind,
                )
            )
    return rows


class WorkspaceFileImpactService:
    """Build a coherent bidirectional graph directly from durable `_repo` bytes."""

    def __init__(
        self,
        repo: RepoStorage | None = None,
        *,
        snapshot_cache: _WorkspaceSnapshotCache | None = None,
    ):
        self.repo = repo or RepoStorage()
        self._snapshot_cache = (
            snapshot_cache
            if snapshot_cache is not None
            else _workspace_snapshot_cache
            if repo is None
            else None
        )

    async def _load_python_snapshot(self) -> _WorkspaceSnapshot:
        started = time.perf_counter()
        snapshot_duration, snapshot_files, _ = _impact_metric_instruments()
        paths: list[str] = []
        success = False
        try:
            paths = sorted(
                path
                for path in await self.repo.list()
                if _is_authored_python_path(path)
            )
            if not paths or len(paths) > MAX_SNAPSHOT_FILES:
                raise WorkspaceFileImpactInvalid(
                    f"workspace Python inventory must contain 1-{MAX_SNAPSHOT_FILES} files"
                )
            read_many = getattr(self.repo, "read_many", None)
            if read_many is not None:
                files = await read_many(paths, concurrency=32)
            else:
                semaphore = asyncio.Semaphore(32)

                async def read(path: str) -> tuple[str, bytes]:
                    async with semaphore:
                        return path, await self.repo.read(path)

                files = dict(await asyncio.gather(*(read(path) for path in paths)))
            hashes = {path: _sha256(raw) for path, raw in files.items()}
            try:
                impact_index = index_workspace_impact(files)
            except PromotionBundleError as exc:
                raise WorkspaceFileImpactInvalid(str(exc)) from exc
            success = True
            return _WorkspaceSnapshot(
                files=files,
                hashes=hashes,
                snapshot_id=snapshot_id(hashes),
                impact_index=impact_index,
            )
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            snapshot_duration.record(elapsed_ms, {"success": success})
            if paths:
                snapshot_files.record(len(paths), {"success": success})
            logger.info(
                "Workspace impact snapshot loaded files=%d success=%s elapsed_ms=%.1f",
                len(paths),
                success,
                elapsed_ms,
            )

    async def _python_snapshot(self) -> _WorkspaceSnapshot:
        if self._snapshot_cache is None:
            return await self._load_python_snapshot()

        from src.core.module_cache import get_workspace_generation

        return await self._snapshot_cache.get(
            generation=get_workspace_generation,
            load=self._load_python_snapshot,
        )

    async def guarded_write(
        self,
        request: WorkspaceFileImpactRequest,
        candidate_id: str,
        write: Callable[[], Awaitable[None]],
    ) -> WorkspaceFileImpactResponse:
        """Recompute and consume one candidate under the source writer barrier."""

        from src.core.module_cache import workspace_source_update

        started = time.perf_counter()
        _, _, barrier_duration = _impact_metric_instruments()
        success = False
        try:
            async with workspace_source_update(
                reason="checked_workspace_file_write",
                changed_paths=[request.path],
            ):
                impact = await self.preview(request, use_cache=False)
                if impact.candidate_id != candidate_id:
                    raise WorkspaceFileImpactConflict(
                        impact.path, candidate_id, impact.candidate_id
                    )
                if not impact.ready_to_write:
                    raise WorkspaceFileImpactBlocked(impact)
                await write()
            success = True
            return impact
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            barrier_duration.record(elapsed_ms, {"success": success})
            logger.info(
                "Checked Workspace write completed path=%s success=%s barrier_ms=%.1f",
                request.path,
                success,
                elapsed_ms,
            )

    async def preview(
        self,
        request: WorkspaceFileImpactRequest,
        *,
        use_cache: bool = True,
    ) -> WorkspaceFileImpactResponse:
        path = normalize_workspace_path(request.path)
        if not path.endswith(".py"):
            raise WorkspaceFileImpactInvalid(
                "Workspace impact analysis currently supports Python files only"
            )
        if not _is_authored_python_path(path):
            roots = ", ".join(sorted(WORKSPACE_IMPORT_ROOTS))
            raise WorkspaceFileImpactInvalid(
                f"Workspace impact analysis supports authored roots only: {roots}"
            )
        workspace = (
            await self._python_snapshot()
            if use_cache
            else await self._load_python_snapshot()
        )
        live = workspace.files
        current = live.get(path)
        if request.content is None:
            if current is None:
                raise WorkspaceFileImpactInvalid(
                    f"workspace file does not exist: {path}"
                )
            proposed = current
        else:
            proposed = request.content.encode("utf-8")

        try:
            if proposed == current:
                analysis = workspace.impact_index.analysis
            elif current is not None:
                analysis = workspace.impact_index.overlay(path, proposed)
            else:
                analysis = analyze_workspace_impact({**live, path: proposed})
        except PromotionBundleError as exc:
            raise WorkspaceFileImpactInvalid(str(exc)) from exc

        forward_distances = transitive_distances(path, analysis.edges)
        reverse_distances = transitive_distances(path, reverse_edges(analysis.edges))
        forward_paths = set(forward_distances) - {path}
        reverse_paths = set(reverse_distances) - {path}
        relevant_paths = {path, *forward_paths, *reverse_paths}

        diagnostics = _diagnostics(
            path=path,
            changed=current != proposed,
            relevant_paths=relevant_paths,
            forward_paths=forward_paths,
            reverse_paths=reverse_paths,
            analysis=analysis,
            base_analysis=workspace.impact_index.analysis,
            proposed_unchanged=request.content is not None and current == proposed,
        )
        diagnostics.extend(
            _symbol_contract_diagnostics(
                path=path,
                current_exists=current is not None,
                changed=current != proposed,
                reverse_paths=reverse_paths,
                analysis=analysis,
                base_analysis=workspace.impact_index.analysis,
            )
        )

        hashes = (
            workspace.hashes
            if proposed == current
            else {**workspace.hashes, path: _sha256(proposed)}
        )
        snapshot = workspace.snapshot_id if proposed == current else snapshot_id(hashes)
        proposed_hash = hashes[path]
        current_hash = _sha256(current) if current is not None else None
        candidate = _candidate_id(
            {
                "schema": IMPACT_SCHEMA,
                "snapshot_id": snapshot,
                "path": path,
                "current_sha256": current_hash,
                "proposed_sha256": proposed_hash,
            }
        )

        return WorkspaceFileImpactResponse(
            candidate_id=candidate,
            snapshot_id=snapshot,
            path=path,
            direction=request.direction,
            proposed=request.content is not None,
            changed=current != proposed,
            current_sha256=current_hash,
            proposed_sha256=proposed_hash,
            forward_dependencies=(
                _members(forward_paths, forward_distances, hashes)
                if request.direction in {"forward", "both"}
                else []
            ),
            reverse_dependencies=(
                _members(reverse_paths, reverse_distances, hashes)
                if request.direction in {"reverse", "both"}
                else []
            ),
            impacted_paths=sorted({path, *reverse_paths}),
            edges=_edge_rows(analysis=analysis, relevant_paths=relevant_paths),
            diagnostics=diagnostics,
            traversal_complete=True,
            analyzed_path_count=len(relevant_paths),
            blocking_diagnostic_count=sum(
                item.severity == "blocker" for item in diagnostics
            ),
            ready_to_write=not any(item.severity == "blocker" for item in diagnostics),
        )


__all__ = [
    "WorkspaceFileImpactBlocked",
    "WorkspaceFileImpactConflict",
    "WorkspaceFileImpactInvalid",
    "WorkspaceFileImpactService",
]
