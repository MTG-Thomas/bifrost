"""Durable-storage impact graph for guarded Workspace Python edits."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from collections.abc import Mapping

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
    analyze_workspace_impact,
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

MAX_IMPACTED_FILES = 200
logger = logging.getLogger(__name__)
workspace_impact_snapshot_duration = None
workspace_impact_snapshot_files = None
workspace_impact_barrier_duration = None


class _WorkspaceSnapshotCache:
    """Single-flight durable snapshots scoped to one workspace generation."""

    def __init__(self) -> None:
        self._generation: str | None = None
        self._snapshot: dict[str, bytes] | None = None
        self._lock = asyncio.Lock()

    async def get(
        self,
        *,
        generation: Callable[[], Awaitable[str]],
        load: Callable[[], Awaitable[dict[str, bytes]]],
    ) -> dict[str, bytes]:
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


def _diagnostics(
    *,
    path: str,
    relevant_paths: set[str],
    reverse_paths: set[str],
    unresolved_imports: Mapping[str, tuple[str, ...]],
    ambiguous_references: Mapping[str, tuple[str, ...]],
    dynamic_importers: frozenset[str],
    dynamic_reference_importers: frozenset[str],
    proposed_unchanged: bool,
) -> list[WorkspaceFileImpactDiagnostic]:
    diagnostics: list[WorkspaceFileImpactDiagnostic] = []
    if len(relevant_paths) > MAX_IMPACTED_FILES:
        diagnostics.append(
            WorkspaceFileImpactDiagnostic(
                code="impact_fanout_exceeded",
                severity="blocker",
                message=(
                    f"transitive impact contains {len(relevant_paths)} files; "
                    "use reviewed comprehensive validation"
                ),
                path=path,
            )
        )
    for affected_path in sorted(relevant_paths):
        ambiguous = ambiguous_references.get(affected_path, ())
        if ambiguous:
            diagnostics.append(
                WorkspaceFileImpactDiagnostic(
                    code="ambiguous_workflow_reference",
                    severity="blocker",
                    message=(
                        "workflow reference resolves to multiple authored files: "
                        + ", ".join(ambiguous)
                    ),
                    path=affected_path,
                )
            )
        unresolved = unresolved_imports.get(affected_path, ())
        if unresolved:
            diagnostics.append(
                WorkspaceFileImpactDiagnostic(
                    code="unresolved_repo_import",
                    severity="blocker",
                    message="unresolved repo-local imports: " + ", ".join(unresolved),
                    path=affected_path,
                )
            )
        if affected_path in dynamic_importers:
            diagnostics.append(
                WorkspaceFileImpactDiagnostic(
                    code="dynamic_import_unresolved",
                    severity="blocker",
                    message="computed dynamic import prevents complete static impact proof",
                    path=affected_path,
                )
            )
        if affected_path in dynamic_reference_importers:
            diagnostics.append(
                WorkspaceFileImpactDiagnostic(
                    code="dynamic_workflow_reference_unresolved",
                    severity="blocker",
                    message=(
                        "computed workflow reference prevents complete static impact proof"
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

    async def _load_python_snapshot(self) -> dict[str, bytes]:
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
                snapshot = await read_many(paths, concurrency=32)
            else:
                semaphore = asyncio.Semaphore(32)

                async def read(path: str) -> tuple[str, bytes]:
                    async with semaphore:
                        return path, await self.repo.read(path)

                snapshot = dict(await asyncio.gather(*(read(path) for path in paths)))
            success = True
            return snapshot
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

    async def _python_snapshot(self) -> dict[str, bytes]:
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
        live = (
            await self._python_snapshot()
            if use_cache
            else await self._load_python_snapshot()
        )
        current = live.get(path)
        if request.content is None:
            if current is None:
                raise WorkspaceFileImpactInvalid(
                    f"workspace file does not exist: {path}"
                )
            proposed = current
        else:
            proposed = request.content.encode("utf-8")

        graph_files = {**live, path: proposed}
        try:
            analysis = analyze_workspace_impact(graph_files)
        except PromotionBundleError as exc:
            raise WorkspaceFileImpactInvalid(str(exc)) from exc

        forward_distances = transitive_distances(path, analysis.edges)
        reverse_distances = transitive_distances(path, reverse_edges(analysis.edges))
        forward_paths = set(forward_distances) - {path}
        reverse_paths = set(reverse_distances) - {path}
        relevant_paths = {path, *forward_paths, *reverse_paths}

        diagnostics = _diagnostics(
            path=path,
            relevant_paths=relevant_paths,
            reverse_paths=reverse_paths,
            unresolved_imports=analysis.unresolved_imports,
            ambiguous_references=analysis.ambiguous_references,
            dynamic_importers=analysis.dynamic_importers,
            dynamic_reference_importers=analysis.dynamic_reference_importers,
            proposed_unchanged=request.content is not None and current == proposed,
        )

        hashes = {item_path: _sha256(raw) for item_path, raw in graph_files.items()}
        snapshot = snapshot_id(hashes)
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
            ready_to_write=not any(item.severity == "blocker" for item in diagnostics),
        )


__all__ = [
    "WorkspaceFileImpactBlocked",
    "WorkspaceFileImpactConflict",
    "WorkspaceFileImpactInvalid",
    "WorkspaceFileImpactService",
]
