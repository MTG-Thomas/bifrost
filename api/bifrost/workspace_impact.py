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
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from bifrost.promotion import (
    PromotionBundleError,
    dependency_edges,
    dependency_edges_for_file,
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
REFERENCE_CONSTANT_RE = re.compile(
    r"(?:^|_)(?:WORKFLOW_ID|WORKFLOW_NAME|FUNCTION_NAME)$"
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
    ambiguous_references: Mapping[str, tuple[str, ...]]
    dynamic_importers: frozenset[str]
    dynamic_reference_importers: frozenset[str]


@dataclass(frozen=True)
class _ParsedReferences:
    identities: frozenset[str]
    references: frozenset[str]
    unresolved_imports: tuple[str, ...]
    dynamic_import: bool
    dynamic_reference: bool


@dataclass(frozen=True)
class WorkspaceImpactIndex:
    """Parsed base graph reusable for one-file overlays."""

    paths: frozenset[str]
    modules: Mapping[str, str]
    imports: Mapping[str, frozenset[str]]
    parsed: Mapping[str, _ParsedReferences]
    analysis: WorkspaceImpactAnalysis

    def overlay(self, path: str, raw: bytes) -> WorkspaceImpactAnalysis:
        """Reparse one existing file without rebuilding the stable base graph."""

        path = normalize_workspace_path(path)
        if path not in self.paths:
            raise WorkspaceImpactError(
                f"incremental impact overlay requires an existing path: {path}"
            )
        parsed = dict(self.parsed)
        parsed[path] = _parse_references(path, raw, modules=self.modules)
        imports = dict(self.imports)
        imports[path] = frozenset(dependency_edges_for_file(path, raw, self.paths))
        return _analysis_from_parts(imports=imports, parsed=parsed)


@dataclass
class _ReferenceAccumulator:
    identities: set[str] = field(default_factory=set)
    references: set[str] = field(default_factory=set)
    unresolved: set[str] = field(default_factory=set)
    dynamic_import: bool = False
    dynamic_reference: bool = False


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


def _record_reference(
    node: ast.AST | None,
    constants: Mapping[str, str],
    accumulator: _ReferenceAccumulator,
) -> None:
    value = _literal_string(node, constants)
    if value is None:
        accumulator.dynamic_reference = True
    else:
        accumulator.references.add(value)


def _scan_import(
    node: ast.Import,
    modules: Mapping[str, str],
    accumulator: _ReferenceAccumulator,
) -> None:
    for alias in node.names:
        root = alias.name.split(".", 1)[0]
        if root in WORKSPACE_IMPORT_ROOTS and not _module_is_available(
            alias.name, modules
        ):
            accumulator.unresolved.add(alias.name)


def _scan_import_from(
    path: str,
    node: ast.ImportFrom,
    modules: Mapping[str, str],
    accumulator: _ReferenceAccumulator,
) -> None:
    module = _absolute_from_module(path, node)
    root = module.split(".", 1)[0] if module else ""
    if root in WORKSPACE_IMPORT_ROOTS and not _module_is_available(module, modules):
        accumulator.unresolved.add(module)


def _scan_dict(
    node: ast.Dict,
    constants: Mapping[str, str],
    accumulator: _ReferenceAccumulator,
) -> None:
    for key_node, value_node in zip(node.keys, node.values, strict=True):
        if _literal_string(key_node, constants) in REFERENCE_FIELDS:
            value = _literal_string(value_node, constants)
            if value is not None:
                accumulator.references.add(value)


def _scan_dynamic_import(
    node: ast.Call,
    call_name: str,
    constants: Mapping[str, str],
    modules: Mapping[str, str],
    accumulator: _ReferenceAccumulator,
) -> None:
    if call_name in {"eval", "exec", "exec_module"}:
        accumulator.dynamic_import = True
        return
    if call_name not in {"import_module", "__import__"}:
        return
    module = _literal_string(node.args[0], constants) if node.args else None
    if module is None:
        accumulator.dynamic_import = True
    elif module.split(".", 1)[0] in WORKSPACE_IMPORT_ROOTS and not _module_is_available(
        module, modules
    ):
        accumulator.unresolved.add(module)


def _scan_call(
    node: ast.Call,
    constants: Mapping[str, str],
    modules: Mapping[str, str],
    accumulator: _ReferenceAccumulator,
) -> None:
    call_parts = _call_parts(node.func)
    call_name = call_parts[-1] if call_parts else ""
    _scan_dynamic_import(node, call_name, constants, modules, accumulator)
    is_workflow_execution = (
        call_name in {"execute", "execute_workflow"} and "workflows" in call_parts[:-1]
    )
    if is_workflow_execution:
        if node.args:
            _record_reference(node.args[0], constants, accumulator)
        elif not any(keyword.arg in REFERENCE_FIELDS for keyword in node.keywords):
            accumulator.dynamic_reference = True
    for keyword in node.keywords:
        if keyword.arg in REFERENCE_FIELDS:
            _record_reference(keyword.value, constants, accumulator)
        elif is_workflow_execution and keyword.arg is None:
            accumulator.dynamic_reference = True


def _scan_entity_decorators(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    constants: Mapping[str, str],
    accumulator: _ReferenceAccumulator,
) -> None:
    for decorator in node.decorator_list:
        if _decorator_name(decorator) not in ENTITY_DECORATORS:
            continue
        accumulator.identities.add(node.name)
        if not isinstance(decorator, ast.Call):
            continue
        for keyword in decorator.keywords:
            if keyword.arg in {"id", "name"}:
                if value := _literal_string(keyword.value, constants):
                    accumulator.identities.add(value)


def _scan_node(
    path: str,
    node: ast.AST,
    constants: Mapping[str, str],
    modules: Mapping[str, str],
    accumulator: _ReferenceAccumulator,
) -> None:
    if isinstance(node, ast.Import):
        _scan_import(node, modules, accumulator)
    elif isinstance(node, ast.ImportFrom):
        _scan_import_from(path, node, modules, accumulator)
    elif isinstance(node, ast.Dict):
        _scan_dict(node, constants, accumulator)
    elif isinstance(node, ast.Call):
        _scan_call(node, constants, modules, accumulator)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _scan_entity_decorators(node, constants, accumulator)


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
    accumulator = _ReferenceAccumulator()

    for node in ast.walk(tree):
        _scan_node(path, node, constants, modules, accumulator)

    for name, value in constants.items():
        if UUID_RE.fullmatch(value) or REFERENCE_CONSTANT_RE.search(name):
            accumulator.references.add(value)
    return _ParsedReferences(
        identities=frozenset(accumulator.identities),
        references=frozenset(accumulator.references),
        unresolved_imports=tuple(sorted(accumulator.unresolved)),
        dynamic_import=accumulator.dynamic_import,
        dynamic_reference=accumulator.dynamic_reference,
    )


def _registry_relationships(
    parsed: Mapping[str, _ParsedReferences],
) -> tuple[set[tuple[str, str]], dict[str, set[str]]]:
    owners_by_identity: dict[str, set[str]] = defaultdict(set)
    for owner_path, item in parsed.items():
        for identity in item.identities:
            owners_by_identity[identity].add(owner_path)

    registry_edges: set[tuple[str, str]] = set()
    ambiguous_references: dict[str, set[str]] = defaultdict(set)
    for importer, item in parsed.items():
        for reference in item.references:
            targets = owners_by_identity.get(reference, set())
            if len(targets) > 1:
                ambiguous_references[importer].add(reference)
            registry_edges.update(
                (importer, target) for target in targets if target != importer
            )
    return registry_edges, ambiguous_references


def _analysis_from_parts(
    *,
    imports: Mapping[str, Iterable[str]],
    parsed: Mapping[str, _ParsedReferences],
) -> WorkspaceImpactAnalysis:
    combined: dict[str, set[str]] = {
        path: set(targets) for path, targets in imports.items()
    }
    registry_edges, ambiguous_references = _registry_relationships(parsed)
    for importer, target in registry_edges:
        combined[importer].add(target)

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
        ambiguous_references={
            path: tuple(sorted(references))
            for path, references in sorted(ambiguous_references.items())
        },
        dynamic_importers=frozenset(
            path for path, item in parsed.items() if item.dynamic_import
        ),
        dynamic_reference_importers=frozenset(
            path for path, item in parsed.items() if item.dynamic_reference
        ),
    )


def index_workspace_impact(contents: Mapping[str, bytes]) -> WorkspaceImpactIndex:
    """Parse one complete snapshot into a reusable impact index."""

    normalized = {normalize_workspace_path(path): raw for path, raw in contents.items()}
    modules = _module_index(normalized)
    imports = {
        path: frozenset(targets)
        for path, targets in dependency_edges(normalized).items()
    }
    parsed = {
        path: _parse_references(path, raw, modules=modules)
        for path, raw in sorted(normalized.items())
    }
    analysis = _analysis_from_parts(imports=imports, parsed=parsed)
    return WorkspaceImpactIndex(
        paths=frozenset(normalized),
        modules=modules,
        imports=imports,
        parsed=parsed,
        analysis=analysis,
    )


def analyze_workspace_impact(
    contents: Mapping[str, bytes],
) -> WorkspaceImpactAnalysis:
    """Build import and literal registry edges for a complete Python snapshot."""

    return index_workspace_impact(contents).analysis


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
    "WorkspaceImpactIndex",
    "analyze_workspace_impact",
    "index_workspace_impact",
    "reverse_edges",
    "transitive_distances",
]
