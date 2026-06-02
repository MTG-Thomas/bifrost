"""Org-level claim registry helpers: load, dependency graph, cycle detection."""

from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.contracts.claims import CustomClaim as CustomClaimDTO
from src.models.orm.custom_claims import CustomClaim as CustomClaimORM


def load_org_claims(db: Session, organization_id: UUID) -> dict[str, CustomClaimDTO]:
    rows = db.execute(
        select(CustomClaimORM).where(CustomClaimORM.organization_id == organization_id)
    ).scalars().all()
    return {r.name: CustomClaimDTO.model_validate(r) for r in rows}


def referenced_claim_names(where: object | None) -> set[str]:
    """Walk an Expr-shaped node and collect every {claims: <name>} reference."""
    found: set[str] = set()
    # Unwrap Expr / RootModel so the walker sees the underlying dict.
    node = getattr(where, "root", where)
    _walk(node, found)
    return found


def _walk(node: object, found: set[str]) -> None:
    if isinstance(node, dict):
        if set(node.keys()) == {"claims"} and isinstance(node["claims"], str):
            found.add(node["claims"])
            return
        for v in node.values():
            _walk(v, found)
        return
    if isinstance(node, list):
        for v in node:
            _walk(v, found)


def claim_dependency_graph(claims: Iterable[CustomClaimDTO]) -> dict[str, set[str]]:
    """Build adjacency: claim_name -> set of other claim names it references."""
    graph: dict[str, set[str]] = {}
    for c in claims:
        graph[c.name] = referenced_claim_names(c.query.where if c.query else None)
    return graph


def _reconstruct_cycle(parent: dict[str, str | None], start: str, end: str) -> list[str]:
    cycle = [start, end]
    node = end
    while parent[node] is not None and parent[node] != start:
        node = parent[node]
        cycle.append(node)
    cycle.append(start)
    return list(reversed(cycle))


def _visit_dependency(
    graph: dict[str, set[str]],
    color: dict[str, int],
    parent: dict[str, str | None],
    node: str,
    dependency: str,
) -> list[str] | None:
    if dependency not in color:
        return None
    if color[dependency] == 1:
        return _reconstruct_cycle(parent, dependency, node)
    if color[dependency] == 0:
        parent[dependency] = node
        return _find_cycle_from(graph, color, parent, dependency)
    return None


def _find_cycle_from(
    graph: dict[str, set[str]],
    color: dict[str, int],
    parent: dict[str, str | None],
    node: str,
) -> list[str] | None:
    color[node] = 1
    for dependency in graph.get(node, ()):
        cycle = _visit_dependency(graph, color, parent, node, dependency)
        if cycle:
            return cycle
    color[node] = 2
    return None


def find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """Return a cycle path if any, else None."""
    WHITE = 0
    color: dict[str, int] = {n: WHITE for n in graph}
    parent: dict[str, str | None] = {n: None for n in graph}

    for node in graph:
        if color[node] == WHITE:
            cycle = _find_cycle_from(graph, color, parent, node)
            if cycle:
                return cycle
    return None
