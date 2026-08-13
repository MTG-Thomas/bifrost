"""Pure bidirectional impact analysis for Workspace Python source.

The same parser is shipped in the downloadable SDK and used by the API.  It
combines ordinary repo-local Python imports with literal workflow registry
references so a caller can inspect both what a file needs and what can be
affected by changing it.
"""

from __future__ import annotations

import ast
import pathlib
import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from typing import Iterable, Mapping

from bifrost.promotion import (
    PromotionBundleError,
    dependency_edges,
    normalize_workspace_path,
)

WORKSPACE_IMPORT_ROOTS = frozenset(
    {
        "agents",
        "apps",
        "features",
        "helpers",
        "integrations",
        "modules",
        "shared",
        "workflows",
    }
)
REFERENCE_FIELDS = frozenset({"workflow_id", "workflow_name", "function_name"})
ENTITY_DECORATORS = frozenset({"workflow", "tool", "data_provider"})
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class WorkspaceImpactError(PromotionBundleError):
    """The workspace graph is ambiguous or cannot be proven statically."""


@dataclass(frozen=True)
class WorkspaceImpactAnalysis:
    """A deterministic importer -> dependency graph and its uncertainty."""

    edges: Mapping[str, frozenset[str]]
    import_edges: frozenset[tuple[str, str]]
    registry_edges: frozenset[tuple[str, str]]
    unresolved_imports: Mapping[str, tuple[str, ...]]
    dynamic_importers: frozenset[str]


@dataclass(frozen=True)
class _ParsedReferences:
    identities: frozenset[str]
    references: frozenset[str]
    unresolved_imports: tuple[str, ...]
    dynamic_import: bool


def _module_name(path: str) -> str:
    parts = list(pathlib.PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _module_index(paths: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_path in paths:
        path = normalize_workspace_path(raw_path)
        if not path.endswith(".py"):
            continue
        module = _module_name(path)
        if not module:
            continue
        if previous := result.get(module):
            raise WorkspaceImpactError(
                f"ambiguous Python module {module!r}: {previous}, {path}"
            )
        result[module] = path
    return result


def _literal_string(node: ast.AST | None, constants: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _top_level_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            constants[target.id] = value.value
    return constants


def _decorator_name(decorator: ast.expr) -> str | None:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _call_parts(node: ast.expr) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return tuple(reversed(parts))


def _absolute_from_module(path: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    current = _module_name(path).split(".")
    if pathlib.PurePosixPath(path).name != "__init__.py":
        current = current[:-1]
    trim = node.level - 1
    base = current[: len(current) - trim] if trim else current
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def _module_is_available(name: str, modules: Mapping[str, str]) -> bool:
    if not name:
        return False
    if name in modules:
        return True
    prefix = f"{name}."
    return any(module.startswith(prefix) for module in modules)


def _parse_references(
    path: str,
    raw: bytes,
    *,
    modules: Mapping[str, str],
) -> _ParsedReferences:
    try:
        tree = ast.parse(raw.decode("utf-8"), filename=path)
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise WorkspaceImpactError(f"cannot parse {path}: {exc}") from exc
    constants = _top_level_constants(tree)
    identities: set[str] = set()
    references: set[str] = set()
    unresolved: set[str] = set()
    dynamic_import = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in WORKSPACE_IMPORT_ROOTS and not _module_is_available(
                    alias.name, modules
                ):
                    unresolved.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = _absolute_from_module(path, node)
            root = module.split(".", 1)[0] if module else ""
            if root in WORKSPACE_IMPORT_ROOTS and not _module_is_available(
                module, modules
            ):
                unresolved.add(module)
        elif isinstance(node, ast.Dict):
            for key_node, value_node in zip(node.keys, node.values, strict=True):
                key = _literal_string(key_node, constants)
                if key in REFERENCE_FIELDS:
                    if value := _literal_string(value_node, constants):
                        references.add(value)
        elif isinstance(node, ast.Call):
            call_parts = _call_parts(node.func)
            call_name = call_parts[-1] if call_parts else ""
            if call_name in {"import_module", "__import__"}:
                module = _literal_string(node.args[0], constants) if node.args else None
                if module is None:
                    dynamic_import = True
                elif (
                    module.split(".", 1)[0] in WORKSPACE_IMPORT_ROOTS
                    and not _module_is_available(module, modules)
                ):
                    unresolved.add(module)
            elif call_name in {"eval", "exec", "exec_module"}:
                dynamic_import = True
            if (
                call_name in {"execute", "execute_workflow"}
                and "workflows" in call_parts[:-1]
                and node.args
            ):
                if value := _literal_string(node.args[0], constants):
                    references.add(value)
            for keyword in node.keywords:
                if keyword.arg in REFERENCE_FIELDS:
                    if value := _literal_string(keyword.value, constants):
                        references.add(value)

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if _decorator_name(decorator) not in ENTITY_DECORATORS:
                    continue
                identities.add(node.name)
                if isinstance(decorator, ast.Call):
                    for keyword in decorator.keywords:
                        if keyword.arg in {"id", "name"}:
                            if value := _literal_string(keyword.value, constants):
                                identities.add(value)

    for name, value in constants.items():
        if UUID_RE.fullmatch(value) or "WORKFLOW" in name:
            references.add(value)
    return _ParsedReferences(
        identities=frozenset(identities),
        references=frozenset(references),
        unresolved_imports=tuple(sorted(unresolved)),
        dynamic_import=dynamic_import,
    )


def analyze_workspace_impact(
    contents: Mapping[str, bytes],
) -> WorkspaceImpactAnalysis:
    """Build import and literal registry edges for a complete Python snapshot."""

    normalized = {
        normalize_workspace_path(path): raw for path, raw in contents.items()
    }
    modules = _module_index(normalized)
    imports = dependency_edges(normalized)
    parsed = {
        path: _parse_references(path, raw, modules=modules)
        for path, raw in sorted(normalized.items())
    }
    identity_counts: Counter[str] = Counter(
        identity for item in parsed.values() for identity in item.identities
    )
    references = {
        reference for item in parsed.values() for reference in item.references
    }
    ambiguous = sorted(
        identity
        for identity, count in identity_counts.items()
        if count > 1 and (UUID_RE.fullmatch(identity) or identity in references)
    )
    if ambiguous:
        raise WorkspaceImpactError(
            "ambiguous referenced workflow identities: " + ", ".join(ambiguous)
        )
    owner_by_identity = {
        identity: path
        for path, item in parsed.items()
        for identity in item.identities
        if identity_counts[identity] == 1
    }

    combined: dict[str, set[str]] = {
        path: set(targets) for path, targets in imports.items()
    }
    registry_edges: set[tuple[str, str]] = set()
    for importer, item in parsed.items():
        for reference in item.references:
            target = owner_by_identity.get(reference)
            if target is None or target == importer:
                continue
            combined[importer].add(target)
            registry_edges.add((importer, target))

    return WorkspaceImpactAnalysis(
        edges={path: frozenset(sorted(targets)) for path, targets in combined.items()},
        import_edges=frozenset(
            (importer, target)
            for importer, targets in imports.items()
            for target in targets
        ),
        registry_edges=frozenset(registry_edges),
        unresolved_imports={
            path: item.unresolved_imports
            for path, item in parsed.items()
            if item.unresolved_imports
        },
        dynamic_importers=frozenset(
            path for path, item in parsed.items() if item.dynamic_import
        ),
    )


def transitive_distances(
    start: str,
    edges: Mapping[str, Iterable[str]],
) -> dict[str, int]:
    """Return shortest transitive distances from ``start`` over ``edges``."""

    distances: dict[str, int] = {start: 0}
    pending = deque([start])
    while pending:
        path = pending.popleft()
        for target in sorted(edges.get(path, ())):
            if target in distances:
                continue
            distances[target] = distances[path] + 1
            pending.append(target)
    return distances


def reverse_edges(
    edges: Mapping[str, Iterable[str]],
) -> dict[str, frozenset[str]]:
    """Invert importer -> dependency edges."""

    result: dict[str, set[str]] = defaultdict(set)
    for importer, dependencies in edges.items():
        for dependency in dependencies:
            result[dependency].add(importer)
    return {path: frozenset(sorted(importers)) for path, importers in result.items()}


__all__ = [
    "WorkspaceImpactAnalysis",
    "WorkspaceImpactError",
    "analyze_workspace_impact",
    "reverse_edges",
    "transitive_distances",
]
