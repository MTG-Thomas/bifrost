"""Durable-storage impact graph for guarded Workspace Python edits."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping

from bifrost.promotion import MAX_SNAPSHOT_FILES, normalize_workspace_path, snapshot_id
from bifrost.workspace_impact import (
    WorkspaceImpactError,
    analyze_workspace_impact,
    reverse_edges,
    transitive_distances,
)
from src.models.contracts.workspace_file_impact import (
    WorkspaceFileImpactDiagnostic,
    WorkspaceFileImpactEdge,
    WorkspaceFileImpactMember,
    WorkspaceFileImpactRequest,
    WorkspaceFileImpactResponse,
)
from src.services.repo_storage import RepoStorage

IMPACT_SCHEMA = "bifrost.workspace-file-impact/v1"
MAX_IMPACTED_FILES = 200


class WorkspaceFileImpactInvalid(ValueError):
    """The requested graph cannot be proven safely."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _candidate_id(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return f"sha256:{_sha256(canonical)}"


class WorkspaceFileImpactService:
    """Build a coherent bidirectional graph directly from durable `_repo` bytes."""

    def __init__(self, repo: RepoStorage | None = None):
        self.repo = repo or RepoStorage()

    async def _python_snapshot(self) -> dict[str, bytes]:
        paths = sorted(path for path in await self.repo.list() if path.endswith(".py"))
        if not paths or len(paths) > MAX_SNAPSHOT_FILES:
            raise WorkspaceFileImpactInvalid(
                f"workspace Python inventory must contain 1-{MAX_SNAPSHOT_FILES} files"
            )
        semaphore = asyncio.Semaphore(32)

        async def read(path: str) -> tuple[str, bytes]:
            async with semaphore:
                return path, await self.repo.read(path)

        return dict(await asyncio.gather(*(read(path) for path in paths)))

    async def preview(
        self, request: WorkspaceFileImpactRequest
    ) -> WorkspaceFileImpactResponse:
        path = normalize_workspace_path(request.path)
        if not path.endswith(".py"):
            raise WorkspaceFileImpactInvalid(
                "Workspace impact analysis currently supports Python files only"
            )
        live = await self._python_snapshot()
        current = live.get(path)
        if request.content is None and current is None:
            raise WorkspaceFileImpactInvalid(f"workspace file does not exist: {path}")
        proposed = (
            request.content.encode("utf-8")
            if request.content is not None
            else current
        )
        if proposed is None:  # defensive; both branches above prove otherwise
            raise WorkspaceFileImpactInvalid(f"workspace file does not exist: {path}")

        graph_files = {**live, path: proposed}
        try:
            analysis = analyze_workspace_impact(graph_files)
        except WorkspaceImpactError as exc:
            raise WorkspaceFileImpactInvalid(str(exc)) from exc

        forward_distances = transitive_distances(path, analysis.edges)
        reverse_distances = transitive_distances(path, reverse_edges(analysis.edges))
        forward_paths = set(forward_distances) - {path}
        reverse_paths = set(reverse_distances) - {path}
        relevant_paths = {path, *forward_paths, *reverse_paths}

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
            unresolved = analysis.unresolved_imports.get(affected_path, ())
            if unresolved:
                diagnostics.append(
                    WorkspaceFileImpactDiagnostic(
                        code="unresolved_repo_import",
                        severity="blocker",
                        message="unresolved repo-local imports: " + ", ".join(unresolved),
                        path=affected_path,
                    )
                )
            if affected_path in analysis.dynamic_importers:
                diagnostics.append(
                    WorkspaceFileImpactDiagnostic(
                        code="dynamic_import_unresolved",
                        severity="blocker",
                        message=(
                            "computed dynamic import prevents complete static impact proof"
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
        if request.content is not None and current == proposed:
            diagnostics.append(
                WorkspaceFileImpactDiagnostic(
                    code="proposed_bytes_unchanged",
                    severity="warning",
                    message="proposed bytes already match durable workspace storage",
                    path=path,
                )
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

        edge_rows: list[WorkspaceFileImpactEdge] = []
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
                edge_rows.append(
                    WorkspaceFileImpactEdge(
                        importer=importer,
                        dependency=dependency,
                        kind=kind,
                    )
                )

        def members(paths: set[str], distances: Mapping[str, int]):
            return [
                WorkspaceFileImpactMember(
                    path=item_path,
                    sha256=hashes[item_path],
                    depth=distances[item_path],
                )
                for item_path in sorted(paths, key=lambda value: (distances[value], value))
            ]

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
                members(forward_paths, forward_distances)
                if request.direction in {"forward", "both"}
                else []
            ),
            reverse_dependencies=(
                members(reverse_paths, reverse_distances)
                if request.direction in {"reverse", "both"}
                else []
            ),
            impacted_paths=sorted({path, *reverse_paths}),
            edges=edge_rows,
            diagnostics=diagnostics,
            ready_to_write=not any(item.severity == "blocker" for item in diagnostics),
        )


__all__ = [
    "WorkspaceFileImpactInvalid",
    "WorkspaceFileImpactService",
]
