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
import symtable
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from bifrost.promotion import (
    PromotionBundleError,
    WorkspaceImportResolver,
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
    module_symbols: Mapping[str, frozenset[str]]
    dynamic_exporters: frozenset[str]
    dynamic_export_contracts: Mapping[str, tuple[str, ...]]
    symbol_imports: Mapping[tuple[str, str], tuple[str, ...]]
    star_import_edges: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class _ParsedReferences:
    imports: frozenset[str]
    identities: frozenset[str]
    references: frozenset[str]
    unresolved_imports: tuple[str, ...]
    dynamic_import: bool
    dynamic_reference: bool
    module_symbols: frozenset[str]
    dynamic_exports: bool
    dynamic_export_contract: tuple[str, ...]
    symbol_imports: Mapping[str, tuple[str, ...]]
    star_imports: frozenset[str]


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
        imports[path] = parsed[path].imports
        return _analysis_from_parts(imports=imports, parsed=parsed)


@dataclass
class _ReferenceAccumulator:
    identities: set[str] = field(default_factory=set)
    references: set[str] = field(default_factory=set)
    unresolved: set[str] = field(default_factory=set)
    dynamic_import: bool = False
    dynamic_reference: bool = False


@dataclass
class _SymbolAccumulator:
    required: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    stars: set[str] = field(default_factory=set)
    has_module_alias: bool = False


@dataclass
class _SymbolScope:
    parent: _SymbolScope | None
    kind: str = "module"
    blocked: set[str] = field(default_factory=set)
    aliases: dict[str, list[tuple[tuple[str, ...], str]]] = field(
        default_factory=lambda: defaultdict(list)
    )


def _module_name(path: str) -> str:
    parts = list(pathlib.PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _module_index(paths: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    identities: dict[str, str] = {}
    for raw_path in paths:
        path = normalize_workspace_path(raw_path)
        identity = unicodedata.normalize("NFC", path).casefold()
        if previous_path := identities.get(identity):
            raise WorkspaceImpactError(
                f"case/Unicode-colliding workspace paths: {previous_path}, {path}"
            )
        identities[identity] = path
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
        if is_workflow_execution and keyword.arg in REFERENCE_FIELDS:
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


def _target_names(node: ast.expr) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return {name for item in node.elts for name in _target_names(item)}
    return set()


class _NamedExpressionCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802
        self.names.update(_target_names(node.target))
        self.visit(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(  # noqa: N802
        self, node: ast.AsyncFunctionDef
    ) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return


def _named_expression_names(node: ast.stmt) -> set[str]:
    collector = _NamedExpressionCollector()
    collector.visit(node)
    return collector.names


def _statement_bindings(node: ast.stmt) -> tuple[set[str], bool]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}, node.name == "__getattr__"
    names: set[str] = set()
    if isinstance(node, ast.Assign):
        names.update(name for target in node.targets for name in _target_names(target))
    elif isinstance(node, ast.AnnAssign):
        names.update(_target_names(node.target))
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        names.update(_target_names(node.target))
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            if item.optional_vars is not None:
                names.update(_target_names(item.optional_vars))
    elif isinstance(node, ast.Import):
        names.update(
            alias.asname or alias.name.split(".", 1)[0] for alias in node.names
        )
    elif isinstance(node, ast.ImportFrom):
        names.update(
            alias.asname or alias.name for alias in node.names if alias.name != "*"
        )
    names.update(_named_expression_names(node))
    return names, False


def _nested_statement_blocks(node: ast.stmt) -> list[list[ast.stmt]]:
    if isinstance(node, (ast.For, ast.AsyncFor, ast.If, ast.While)):
        return [node.body, node.orelse]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return [node.body]
    if isinstance(node, (ast.Try, ast.TryStar)):
        return [
            node.body,
            *(handler.body for handler in node.handlers),
            node.orelse,
            node.finalbody,
        ]
    if isinstance(node, ast.Match):
        return [case.body for case in node.cases]
    return []


def _top_level_symbols(
    tree: ast.Module,
) -> tuple[frozenset[str], tuple[str, ...]]:
    symbols: set[str] = set()
    dynamic_export_contract: set[str] = set()
    pending = [tree.body]
    while pending:
        for node in pending.pop():
            bound, dynamic = _statement_bindings(node)
            symbols.update(bound)
            if dynamic:
                dynamic_export_contract.add(ast.dump(node, include_attributes=False))
            pending.extend(_nested_statement_blocks(node))
    return frozenset(symbols), tuple(sorted(dynamic_export_contract))


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return tuple(reversed(parts))
    return ()


class _SymbolUseVisitor(ast.NodeVisitor):
    """Resolve imported-module attribute uses with Python lexical shadowing."""

    def __init__(
        self,
        path: str,
        modules: Mapping[str, str],
        symbol_table: symtable.SymbolTable,
    ) -> None:
        self.path = path
        self.modules = modules
        self.accumulator = _SymbolAccumulator()
        self.scope = _SymbolScope(parent=None, kind="module")
        self.symbol_table = symbol_table
        self._table_stack = [symbol_table]
        self._named_expression_binding_scope: _SymbolScope | None = None

    @staticmethod
    def _blocked_names(table: symtable.SymbolTable) -> set[str]:
        return {
            name
            for name in table.get_identifiers()
            if (
                table.lookup(name).is_local()
                or table.lookup(name).is_parameter()
                or table.lookup(name).is_imported()
            )
            and not table.lookup(name).is_global()
            and not table.lookup(name).is_nonlocal()
        }

    def _child_table(
        self, name: str, lineno: int
    ) -> tuple[symtable.SymbolTable, symtable.SymbolTable | None]:
        matches = [
            table
            for table in self._table_stack[-1].get_children()
            if table.get_name() == name and table.get_lineno() == lineno
        ]
        wrapper = None
        if len(matches) == 1 and matches[0].get_type() == "type parameters":
            wrapper = matches[0]
            matches = [
                table
                for table in wrapper.get_children()
                if table.get_name() == name and table.get_lineno() == lineno
            ]
        if len(matches) != 1:
            raise WorkspaceImpactError(
                f"cannot resolve lexical scope {name!r} at {self.path}:{lineno}"
            )
        return matches[0], wrapper

    def _type_parameter_scope(
        self,
        parent: _SymbolScope | None,
        wrapper: symtable.SymbolTable | None,
    ) -> _SymbolScope | None:
        if wrapper is None:
            return parent
        return _SymbolScope(
            parent=parent,
            kind="type_parameters",
            blocked=self._blocked_names(wrapper),
        )

    def _bind_alias(self, name: str, prefix: tuple[str, ...], target: str) -> None:
        self.scope.aliases[name].append((prefix, target))

    def _clear_bound_aliases(self, names: Iterable[str]) -> None:
        self._clear_scope_aliases(self.scope, names)

    @staticmethod
    def _clear_scope_aliases(scope: _SymbolScope, names: Iterable[str]) -> None:
        for name in names:
            scope.aliases.pop(name, None)
            scope.blocked.add(name)

    def _resolve(self, chain: tuple[str, ...]) -> list[tuple[tuple[str, ...], str]]:
        scope: _SymbolScope | None = self.scope
        while scope is not None:
            if aliases := scope.aliases.get(chain[0]):
                return aliases
            if chain[0] in scope.blocked:
                return []
            scope = scope.parent
        return []

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        chain = _attribute_chain(node)
        if chain:
            for prefix, target in self._resolve(chain):
                if len(chain) > len(prefix) and chain[: len(prefix)] == prefix:
                    self.accumulator.required[target].add(chain[len(prefix)])
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            target = self.modules.get(alias.name)
            bound = alias.asname or alias.name.split(".", 1)[0]
            self._clear_bound_aliases({bound})
            if target is None:
                continue
            prefix = (bound,) if alias.asname else tuple(alias.name.split("."))
            self._bind_alias(bound, prefix, target)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = _absolute_from_module(self.path, node)
        target = self.modules.get(module)
        if target is not None:
            for alias in node.names:
                if alias.name == "*":
                    self.accumulator.stars.add(target)
                else:
                    self.accumulator.required[target].add(alias.name)
            return
        for alias in node.names:
            candidate = f"{module}.{alias.name}" if module else alias.name
            target = self.modules.get(candidate)
            bound = alias.asname or alias.name
            self._clear_bound_aliases({bound})
            if target is not None:
                self._bind_alias(bound, (bound,), target)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.args.vararg is not None and node.args.vararg.annotation is not None:
            self.visit(node.args.vararg.annotation)
        if node.args.kwarg is not None and node.args.kwarg.annotation is not None:
            self.visit(node.args.kwarg.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        containing_scope = self.scope
        lookup_parent = (
            containing_scope.parent
            if containing_scope.kind == "class"
            else containing_scope
        )
        table, wrapper = self._child_table(node.name, node.lineno)
        lookup_parent = self._type_parameter_scope(lookup_parent, wrapper)
        self.scope = _SymbolScope(
            parent=lookup_parent,
            kind="function",
            blocked=self._blocked_names(table),
        )
        self._table_stack.append(table)
        for item in node.body:
            self.visit(item)
        self._table_stack.pop()
        self.scope = containing_scope
        self._clear_bound_aliases({node.name})

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_AsyncFunctionDef(  # noqa: N802
        self, node: ast.AsyncFunctionDef
    ) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        containing_scope = self.scope
        lookup_parent = (
            containing_scope.parent
            if containing_scope.kind == "class"
            else containing_scope
        )
        table, _ = self._child_table("lambda", node.lineno)
        self.scope = _SymbolScope(
            parent=lookup_parent,
            kind="function",
            blocked=self._blocked_names(table),
        )
        self._table_stack.append(table)
        self.visit(node.body)
        self._table_stack.pop()
        self.scope = containing_scope

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        for item in (*node.decorator_list, *node.bases, *node.keywords):
            self.visit(item.value if isinstance(item, ast.keyword) else item)
        parent = self.scope
        table, wrapper = self._child_table(node.name, node.lineno)
        lookup_parent = self._type_parameter_scope(parent, wrapper)
        self.scope = _SymbolScope(parent=lookup_parent, kind="class")
        self._table_stack.append(table)
        for item in node.body:
            self.visit(item)
        self._table_stack.pop()
        self.scope = parent
        self._clear_bound_aliases({node.name})

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        self.visit(node.value)
        self._clear_bound_aliases(
            name for target in node.targets for name in _target_names(target)
        )

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if node.value is not None:
            self.visit(node.value)
        self.visit(node.annotation)
        self._clear_bound_aliases(_target_names(node.target))

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802
        self.visit(node.value)
        target_scope = self._named_expression_binding_scope or self.scope
        self._clear_scope_aliases(target_scope, _target_names(node.target))

    def _visit_loop(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        self._clear_scope_aliases(self.scope, _target_names(node.target))
        for item in (*node.body, *node.orelse):
            self.visit(item)

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802
        self._visit_loop(node)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._clear_scope_aliases(
                    self.scope,
                    _target_names(item.optional_vars),
                )
        for item in node.body:
            self.visit(item)

    def visit_With(self, node: ast.With) -> None:  # noqa: N802
        self._visit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
        self._visit_with(node)

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        values: Iterable[ast.expr],
    ) -> None:
        containing_scope = self.scope
        self.scope = _SymbolScope(parent=containing_scope, kind="comprehension")
        previous_binding_scope = self._named_expression_binding_scope
        self._named_expression_binding_scope = containing_scope
        for generator in generators:
            self.visit(generator.iter)
            self.scope.blocked.update(_target_names(generator.target))
            for condition in generator.ifs:
                self.visit(condition)
        for value in values:
            self.visit(value)
        self._named_expression_binding_scope = previous_binding_scope
        self.scope = containing_scope

    def visit_ListComp(self, node: ast.ListComp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, [node.elt])

    def visit_SetComp(self, node: ast.SetComp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, [node.elt])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, [node.elt])

    def visit_DictComp(self, node: ast.DictComp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, [node.key, node.value])


def _symbol_requirements(
    tree: ast.Module,
    *,
    path: str,
    modules: Mapping[str, str],
    source: str,
    seed: _SymbolAccumulator,
) -> tuple[dict[str, tuple[str, ...]], frozenset[str]]:
    if not seed.has_module_alias:
        return (
            {target: tuple(sorted(names)) for target, names in seed.required.items()},
            frozenset(seed.stars),
        )
    try:
        table = symtable.symtable(source, path, "exec")
    except SyntaxError as exc:
        raise WorkspaceImpactError(f"cannot resolve scopes in {path}: {exc}") from exc
    visitor = _SymbolUseVisitor(path, modules, table)
    visitor.visit(tree)
    return (
        {
            target: tuple(sorted(names))
            for target, names in visitor.accumulator.required.items()
        },
        frozenset(visitor.accumulator.stars),
    )


def _scan_symbol_import_seed(
    node: ast.AST,
    *,
    path: str,
    modules: Mapping[str, str],
    seed: _SymbolAccumulator,
) -> None:
    if isinstance(node, ast.Import):
        seed.has_module_alias = seed.has_module_alias or any(
            alias.name in modules for alias in node.names
        )
        return
    if not isinstance(node, ast.ImportFrom):
        return
    module = _absolute_from_module(path, node)
    if target := modules.get(module):
        for alias in node.names:
            if alias.name == "*":
                seed.stars.add(target)
            else:
                seed.required[target].add(alias.name)
        return
    seed.has_module_alias = seed.has_module_alias or any(
        (f"{module}.{alias.name}" if module else alias.name) in modules
        for alias in node.names
    )


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
        source = raw.decode("utf-8")
        tree = ast.parse(source, filename=path)
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise WorkspaceImpactError(f"cannot parse {path}: {exc}") from exc
    constants = _top_level_constants(tree)
    module_symbols, dynamic_export_contract = _top_level_symbols(tree)
    accumulator = _ReferenceAccumulator()
    symbol_seed = _SymbolAccumulator()
    import_resolver = WorkspaceImportResolver(path, modules)

    for node in ast.walk(tree):
        import_resolver.scan(node)
        _scan_node(path, node, constants, modules, accumulator)
        _scan_symbol_import_seed(
            node,
            path=path,
            modules=modules,
            seed=symbol_seed,
        )

    symbol_imports, star_imports = _symbol_requirements(
        tree,
        path=path,
        modules=modules,
        source=source,
        seed=symbol_seed,
    )

    for name, value in constants.items():
        if UUID_RE.fullmatch(value) or REFERENCE_CONSTANT_RE.search(name):
            accumulator.references.add(value)
    return _ParsedReferences(
        imports=frozenset(import_resolver.result()),
        identities=frozenset(accumulator.identities),
        references=frozenset(accumulator.references),
        unresolved_imports=tuple(sorted(accumulator.unresolved)),
        dynamic_import=accumulator.dynamic_import,
        dynamic_reference=accumulator.dynamic_reference,
        module_symbols=module_symbols,
        dynamic_exports=bool(dynamic_export_contract),
        dynamic_export_contract=dynamic_export_contract,
        symbol_imports=symbol_imports,
        star_imports=star_imports,
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
        module_symbols={path: item.module_symbols for path, item in parsed.items()},
        dynamic_exporters=frozenset(
            path for path, item in parsed.items() if item.dynamic_exports
        ),
        dynamic_export_contracts={
            path: item.dynamic_export_contract
            for path, item in parsed.items()
            if item.dynamic_export_contract
        },
        symbol_imports={
            (importer, target): names
            for importer, item in parsed.items()
            for target, names in item.symbol_imports.items()
        },
        star_import_edges=frozenset(
            (importer, target)
            for importer, item in parsed.items()
            for target in item.star_imports
        ),
    )


def index_workspace_impact(contents: Mapping[str, bytes]) -> WorkspaceImpactIndex:
    """Parse one complete snapshot into a reusable impact index."""

    normalized = {normalize_workspace_path(path): raw for path, raw in contents.items()}
    modules = _module_index(normalized)
    parsed = {
        path: _parse_references(path, raw, modules=modules)
        for path, raw in sorted(normalized.items())
    }
    imports = {path: item.imports for path, item in parsed.items()}
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
