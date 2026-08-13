#!/usr/bin/env python3
"""Build an inspectable, fail-closed test plan for one exact Git diff.

The planner models Python and TypeScript imports in both directions.  Tests own
source only when their dependency closure reaches it; backend e2e tests also
own FastAPI router modules when their literal request paths reach a route in
that module.  This catches shared-helper fan-out without making every leaf
change run every platform suite.

Unknown paths, deletions, mixed backend/client changes, contract/storage
boundaries, parse failures, excessive fan-out, and untested downstream source
all select comprehensive validation.  CI uploads the JSON result so a focused
run is reviewable rather than an opaque optimization.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[2]

PYTHON_SOURCE_PREFIXES = ("api/src/", "api/shared/", "api/bifrost/", "api/scripts/")
PYTHON_TEST_PREFIXES = ("api/tests/unit/", "api/tests/e2e/")
CLIENT_SOURCE_PREFIX = "client/src/"
CLIENT_UNIT_SUFFIXES = (".test.ts", ".test.tsx")
CLIENT_E2E_PREFIX = "client/e2e/"
CLIENT_SOURCE_SUFFIXES = (".ts", ".tsx")

DOC_ROOTS = ("docs/", ".agents/", ".claude/", ".codex/", "plugins/bifrost/skills/")
DOC_FILES = frozenset(
    {
        "AGENTS.md",
        "CLAUDE.md",
        "CONTRIBUTING.md",
        "GOVERNANCE.md",
        "CODE_OF_CONDUCT.md",
        "README.md",
        "SECURITY.md",
        "LICENSE",
    }
)

# These surfaces can alter generated contracts, storage, dependency resolution,
# release behavior, or the planner itself.  A partial graph is not credible.
COMPREHENSIVE_PREFIXES = (
    ".github/",
    "api/alembic/",
    "api/src/models/contracts/",
    "api/src/models/orm/",
    "k8s/",
    "scripts/ci/",
)
COMPREHENSIVE_FILES = frozenset(
    {
        "pyproject.toml",
        "requirements.lock",
        "requirements-piptools.lock",
        "requirements-pyright.lock",
        "client/package.json",
        "client/package-lock.json",
        "client/src/lib/v1.d.ts",
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "docker-compose.test.yml",
        "test.sh",
    }
)
COMPREHENSIVE_BASENAMES = frozenset(
    {"Dockerfile", "Dockerfile.dev", "Dockerfile.playwright", "pyrightconfig.json"}
)

# Import-only assembly roots do not become semantic downstream owners of every
# router/consumer they register.  The registered module still needs direct test
# ownership, including an e2e route owner for router boundaries.
PYTHON_WIRING_SINKS = frozenset(
    {
        "api/src/main.py",
        "api/src/worker/main.py",
        "api/src/scheduler/main.py",
    }
)
CLIENT_WIRING_SINKS = frozenset(
    {
        "client/src/main.tsx",
        "client/src/App.tsx",
        "client/src/routes.tsx",
    }
)

PYTHON_E2E_BOUNDARIES = (
    "api/src/routers/",
    "api/src/jobs/",
    "api/src/worker/",
    "api/src/scheduler/",
)
MCP_BOUNDARY_MARKERS = (
    "/mcp_",
    "/mcp/",
    "mcp_server",
    "mcp_client",
    "mcp_gateway",
    "external_mcp",
)
CLIENT_E2E_BOUNDARIES = ("client/src/pages/", "client/src/App.tsx", "client/src/routes")

MAX_CHANGED_SOURCE = 20
MAX_IMPACTED_SOURCE = 180
MAX_SELECTED_TESTS = 120

IMPORT_FROM_RE = re.compile(r"\bfrom\s*['\"]([^'\"]+)['\"]")
SIDE_EFFECT_IMPORT_RE = re.compile(r"\bimport\s*['\"]([^'\"]+)['\"]")
DYNAMIC_IMPORT_RE = re.compile(r"\bimport\s*\(\s*['\"]([^'\"]+)['\"]\s*\)")
ROUTE_PARAM_RE = re.compile(r"\{[^/]+\}")


class PlanError(RuntimeError):
    """The dependency model could not safely represent the change."""


@dataclass(frozen=True)
class GitChange:
    status: str
    path: str


@dataclass(frozen=True)
class PythonNode:
    imports: frozenset[str]
    routes: frozenset[str]
    requests: frozenset[str]


@dataclass
class SurfacePlan:
    changed: tuple[str, ...] = ()
    impacted: tuple[str, ...] = ()
    unit_tests: tuple[str, ...] = ()
    e2e_tests: tuple[str, ...] = ()
    uncovered: tuple[str, ...] = ()
    dependency_edges: int = 0
    runtime_edges: int = 0


@dataclass
class AffectedPlan:
    scope: str
    reason: str
    changed_paths: tuple[str, ...]
    python: SurfacePlan = field(default_factory=SurfacePlan)
    client: SurfacePlan = field(default_factory=SurfacePlan)

    def lane(self, name: str) -> str:
        if self.scope == "comprehensive":
            return "comprehensive"
        if self.scope == "docs-only":
            return "skip"
        if name == "api_quality":
            return (
                "affected"
                if (
                    self.python.changed
                    or self.python.unit_tests
                    or self.python.e2e_tests
                )
                else "skip"
            )
        if name == "api_unit":
            return "affected" if self.python.unit_tests else "skip"
        if name == "api_e2e":
            return "affected" if self.python.e2e_tests else "skip"
        if name == "client_quality":
            return (
                "affected"
                if (
                    self.client.changed
                    or self.client.unit_tests
                    or self.client.e2e_tests
                )
                else "skip"
            )
        if name == "client_unit":
            return "affected" if self.client.unit_tests else "skip"
        if name == "client_e2e":
            return "affected" if self.client.e2e_tests else "skip"
        if name == "mcp_conformance":
            paths = (
                *self.python.changed,
                *self.python.impacted,
                *self.python.unit_tests,
                *self.python.e2e_tests,
            )
            return "affected" if any(_is_mcp_path(path) for path in paths) else "skip"
        raise KeyError(name)

    def to_dict(self) -> dict[str, object]:
        lanes = {
            name: self.lane(name)
            for name in (
                "api_quality",
                "api_unit",
                "api_e2e",
                "client_quality",
                "client_unit",
                "client_e2e",
                "mcp_conformance",
            )
        }
        return {
            "schema": "bifrost.affected-tests/v1",
            "scope": self.scope,
            "reason": self.reason,
            "changed_paths": list(self.changed_paths),
            "python": vars(self.python),
            "client": vars(self.client),
            "lanes": lanes,
        }


def _normalize(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _is_docs(path: str) -> bool:
    return path in DOC_FILES or path.startswith(DOC_ROOTS)


def _is_python_source(path: str) -> bool:
    return path.endswith(".py") and path.startswith(PYTHON_SOURCE_PREFIXES)


def _is_python_test(path: str) -> bool:
    return (
        path.endswith(".py")
        and path.startswith(PYTHON_TEST_PREFIXES)
        and PurePosixPath(path).name.startswith("test_")
    )


def _is_client_unit(path: str) -> bool:
    return path.startswith(CLIENT_SOURCE_PREFIX) and path.endswith(CLIENT_UNIT_SUFFIXES)


def _is_client_source(path: str) -> bool:
    return (
        path.startswith(CLIENT_SOURCE_PREFIX)
        and path.endswith(CLIENT_SOURCE_SUFFIXES)
        and not _is_client_unit(path)
        and not path.endswith(".d.ts")
    )


def _is_client_e2e(path: str) -> bool:
    return path.startswith(CLIENT_E2E_PREFIX) and path.endswith(".spec.ts")


def _is_comprehensive_path(path: str) -> bool:
    return (
        path in COMPREHENSIVE_FILES
        or path.startswith(COMPREHENSIVE_PREFIXES)
        or PurePosixPath(path).name in COMPREHENSIVE_BASENAMES
    )


def _is_mcp_path(path: str) -> bool:
    lowered = f"/{path.lower()}"
    return any(marker in lowered for marker in MCP_BOUNDARY_MARKERS)


def git_changes(base: str, head: str) -> tuple[GitChange, ...]:
    result = subprocess.run(
        [
            "git",
            "diff",
            "--name-status",
            "--no-renames",
            "--diff-filter=ACMRD",
            f"{base}...{head}",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    changes: list[GitChange] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        status, path = line.split("\t", 1)
        changes.append(GitChange(status=status, path=_normalize(path)))
    return tuple(changes)


def _files(prefixes: Sequence[str], suffixes: tuple[str, ...]) -> tuple[str, ...]:
    paths: set[str] = set()
    for prefix in prefixes:
        root = REPO_ROOT / prefix.rstrip("/")
        if root.is_dir():
            paths.update(
                path.relative_to(REPO_ROOT).as_posix()
                for path in root.rglob("*")
                if path.is_file() and path.name.endswith(suffixes)
            )
    return tuple(sorted(paths))


def _python_module(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] == "api":
        parts.pop(0)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _python_index(paths: Iterable[str]) -> dict[str, str]:
    return {module: path for path in paths if (module := _python_module(path))}


def _resolve_python_module(
    module: str,
    imported_names: Sequence[str],
    index: Mapping[str, str],
) -> set[str]:
    resolved: set[str] = set()
    if path := index.get(module):
        resolved.add(path)
    for name in imported_names:
        if name != "*" and (path := index.get(f"{module}.{name}")):
            resolved.add(path)
    return resolved


def _literal_path(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("{value}")
        return "".join(parts)
    return None


def _python_node(path: str, index: Mapping[str, str]) -> PythonNode:
    try:
        tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"), filename=path)
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise PlanError(f"unable to parse {path}: {exc}") from exc
    imports: set[str] = set()
    routes: set[str] = set()
    requests: set[str] = set()
    package = _python_module(path).split(".")[:-1]
    router_prefixes: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.update(_resolve_python_module(alias.name, (), index))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                retained = len(package) - (node.level - 1)
                if retained < 0:
                    continue
                module_parts = package[:retained]
                if node.module:
                    module_parts.extend(node.module.split("."))
                module = ".".join(module_parts)
            else:
                module = node.module or ""
            imports.update(
                _resolve_python_module(
                    module, [alias.name for alias in node.names], index
                )
            )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if isinstance(value, ast.Call) and (
                (isinstance(value.func, ast.Name) and value.func.id == "APIRouter")
                or (
                    isinstance(value.func, ast.Attribute)
                    and value.func.attr == "APIRouter"
                )
            ):
                prefix = ""
                for keyword in value.keywords:
                    if (
                        keyword.arg == "prefix"
                        and (literal := _literal_path(keyword.value)) is not None
                    ):
                        prefix = literal
                for target in targets:
                    if isinstance(target, ast.Name):
                        router_prefixes[target.id] = prefix
        elif isinstance(node, ast.Call):
            call_name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            if (
                call_name == "import_module"
                and node.args
                and (module := _literal_path(node.args[0]))
            ):
                imports.update(_resolve_python_module(module, (), index))
            if (
                call_name
                in {"get", "post", "put", "patch", "delete", "options", "head"}
                and node.args
            ):
                if route := _literal_path(node.args[0]):
                    requests.add(route)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(
                decorator.func, ast.Attribute
            ):
                continue
            owner = decorator.func.value
            if (
                not isinstance(owner, ast.Name)
                or owner.id not in router_prefixes
                or not decorator.args
            ):
                continue
            if decorator.func.attr not in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
                "options",
                "head",
                "api_route",
            }:
                continue
            if suffix := _literal_path(decorator.args[0]):
                routes.add(f"{router_prefixes[owner.id]}{suffix}")
            elif suffix == "":
                routes.add(router_prefixes[owner.id])
    return PythonNode(frozenset(imports), frozenset(routes), frozenset(requests))


def _route_matches(route: str, request: str) -> bool:
    route = route.split("?", 1)[0].rstrip("/") or "/"
    request = request.split("?", 1)[0].rstrip("/") or "/"
    pattern = "^" + re.escape(route).replace(r"\{", "{").replace(r"\}", "}") + "$"
    pattern = ROUTE_PARAM_RE.sub("[^/]+", pattern)
    return re.fullmatch(pattern, request) is not None


def _transitive(
    start: Iterable[str],
    dependencies: Mapping[str, set[str]],
    wiring_sinks: frozenset[str] = frozenset(),
) -> set[str]:
    seen: set[str] = set()
    pending = list(start)
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        if path in wiring_sinks or path.endswith("/__init__.py"):
            continue
        pending.extend(dependencies.get(path, ()))
    return seen


def _reverse_closure(
    changed: Iterable[str],
    dependencies: Mapping[str, set[str]],
    wiring_sinks: frozenset[str],
) -> set[str]:
    reverse: dict[str, set[str]] = defaultdict(set)
    for importer, imported in dependencies.items():
        if importer in wiring_sinks or importer.endswith("/__init__.py"):
            continue
        for target in imported:
            reverse[target].add(importer)
    seen: set[str] = set()
    pending = deque(changed)
    while pending:
        path = pending.popleft()
        if path in seen:
            continue
        seen.add(path)
        pending.extend(reverse.get(path, ()))
        if len(seen) > MAX_IMPACTED_SOURCE:
            break
    return seen


def _relative_test(path: str) -> str:
    return path.removeprefix("api/")


def _plan_python(
    changed_source: Sequence[str], changed_tests: Sequence[str]
) -> SurfacePlan:
    source_files = _files(PYTHON_SOURCE_PREFIXES, (".py",))
    test_files = tuple(
        path for path in _files(PYTHON_TEST_PREFIXES, (".py",)) if _is_python_test(path)
    )
    available = frozenset(source_files)
    index = _python_index(source_files)
    parsed = {path: _python_node(path, index) for path in (*source_files, *test_files)}
    dependencies = {
        path: set(node.imports) for path, node in parsed.items() if path in available
    }
    impacted = _reverse_closure(changed_source, dependencies, PYTHON_WIRING_SINKS)

    test_direct: dict[str, set[str]] = {
        path: set(parsed[path].imports) for path in test_files
    }
    runtime_edges = 0
    routers = {
        path: parsed[path].routes for path in source_files if parsed[path].routes
    }
    for test in test_files:
        if not test.startswith("api/tests/e2e/"):
            continue
        for router, routes in routers.items():
            if any(
                _route_matches(route, request)
                for route in routes
                for request in parsed[test].requests
            ):
                if router not in test_direct[test]:
                    test_direct[test].add(router)
                    runtime_edges += 1

    closures = {
        test: _transitive(direct, dependencies, PYTHON_WIRING_SINKS)
        for test, direct in test_direct.items()
    }
    selected = set(changed_tests)
    selected.update(
        test for test, closure in closures.items() if closure & set(changed_source)
    )
    covered = (
        set().union(*(closures.get(test, set()) for test in selected))
        if selected
        else set()
    )
    uncovered = impacted - covered
    unit = tuple(
        sorted(
            _relative_test(path)
            for path in selected
            if path.startswith("api/tests/unit/")
        )
    )
    e2e = tuple(
        sorted(
            _relative_test(path)
            for path in selected
            if path.startswith("api/tests/e2e/")
        )
    )

    boundary_impacted = {
        path for path in impacted if path.startswith(PYTHON_E2E_BOUNDARIES)
    }
    e2e_covered = (
        set().union(*(closures.get(f"api/{path}", set()) for path in e2e))
        if e2e
        else set()
    )
    uncovered_boundary = boundary_impacted - e2e_covered
    uncovered.update(uncovered_boundary)

    return SurfacePlan(
        changed=tuple(sorted(changed_source)),
        impacted=tuple(sorted(impacted)),
        unit_tests=unit,
        e2e_tests=e2e,
        uncovered=tuple(sorted(uncovered)),
        dependency_edges=sum(len(items) for items in dependencies.values()),
        runtime_edges=runtime_edges,
    )


def _client_candidates(
    path: str, specifier: str, available: frozenset[str]
) -> set[str]:
    if specifier.startswith("@/"):
        raw = f"client/src/{specifier[2:]}"
    elif specifier.startswith("."):
        raw = str(PurePosixPath(path).parent.joinpath(specifier))
    else:
        return set()
    raw = str(PurePosixPath(raw))
    candidates = {raw}
    candidates.update(f"{raw}{suffix}" for suffix in (".ts", ".tsx"))
    candidates.update(f"{raw}/index{suffix}" for suffix in (".ts", ".tsx"))
    return candidates & available


def _client_imports(path: str, available: frozenset[str]) -> set[str]:
    try:
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PlanError(f"unable to read {path}: {exc}") from exc
    specifiers = set(IMPORT_FROM_RE.findall(text))
    specifiers.update(SIDE_EFFECT_IMPORT_RE.findall(text))
    specifiers.update(DYNAMIC_IMPORT_RE.findall(text))
    resolved: set[str] = set()
    for specifier in specifiers:
        resolved.update(_client_candidates(path, specifier, available))
    return resolved


def _plan_client(
    changed_source: Sequence[str], changed_tests: Sequence[str]
) -> SurfacePlan:
    all_files = _files((CLIENT_SOURCE_PREFIX,), CLIENT_SOURCE_SUFFIXES)
    source_files = tuple(path for path in all_files if _is_client_source(path))
    unit_files = tuple(path for path in all_files if _is_client_unit(path))
    e2e_files = _files((CLIENT_E2E_PREFIX,), (".spec.ts",))
    available = frozenset(source_files)
    dependencies = {path: _client_imports(path, available) for path in source_files}
    impacted = _reverse_closure(changed_source, dependencies, CLIENT_WIRING_SINKS)
    test_direct = {
        path: _client_imports(path, available) for path in (*unit_files, *e2e_files)
    }
    closures = {
        test: _transitive(direct, dependencies, CLIENT_WIRING_SINKS)
        for test, direct in test_direct.items()
    }
    selected = set(changed_tests)
    selected.update(
        test for test, closure in closures.items() if closure & set(changed_source)
    )
    covered = (
        set().union(*(closures.get(test, set()) for test in selected))
        if selected
        else set()
    )
    uncovered = impacted - covered

    # Browser specs do not normally import React pages.  Page/router changes
    # therefore fail closed to the comprehensive browser lane unless explicit
    # source ownership exists.
    if any(path.startswith(CLIENT_E2E_BOUNDARIES) for path in impacted):
        e2e_selected = {path for path in selected if _is_client_e2e(path)}
        if not e2e_selected:
            uncovered.update(
                path for path in impacted if path.startswith(CLIENT_E2E_BOUNDARIES)
            )

    return SurfacePlan(
        changed=tuple(sorted(changed_source)),
        impacted=tuple(sorted(impacted)),
        unit_tests=tuple(
            sorted(
                path.removeprefix("client/")
                for path in selected
                if _is_client_unit(path)
            )
        ),
        e2e_tests=tuple(
            sorted(
                path.removeprefix("client/")
                for path in selected
                if _is_client_e2e(path)
            )
        ),
        uncovered=tuple(sorted(uncovered)),
        dependency_edges=sum(len(items) for items in dependencies.values()),
    )


def _comprehensive(
    changes: Sequence[GitChange], reason: str, **surfaces: SurfacePlan
) -> AffectedPlan:
    return AffectedPlan(
        scope="comprehensive",
        reason=reason,
        changed_paths=tuple(sorted(change.path for change in changes)),
        python=surfaces.get("python", SurfacePlan()),
        client=surfaces.get("client", SurfacePlan()),
    )


def plan_changes(changes: Sequence[GitChange]) -> AffectedPlan:
    changes = tuple(
        GitChange(change.status, _normalize(change.path)) for change in changes
    )
    if not changes:
        return _comprehensive(changes, "empty diff")
    if any(change.status == "D" for change in changes):
        return _comprehensive(changes, "deletions require comprehensive validation")
    if risky := sorted(
        change.path for change in changes if _is_comprehensive_path(change.path)
    ):
        return _comprehensive(
            changes, f"high-risk or CI-owned paths changed: {', '.join(risky[:5])}"
        )

    def supported(path: str) -> bool:
        return (
            _is_docs(path)
            or _is_python_source(path)
            or _is_python_test(path)
            or _is_client_source(path)
            or _is_client_unit(path)
            or _is_client_e2e(path)
        )

    if unknown := sorted(
        change.path for change in changes if not supported(change.path)
    ):
        return _comprehensive(
            changes, f"unmodelled changed paths: {', '.join(unknown[:5])}"
        )

    python_source = tuple(
        sorted(change.path for change in changes if _is_python_source(change.path))
    )
    python_tests = tuple(
        sorted(change.path for change in changes if _is_python_test(change.path))
    )
    client_source = tuple(
        sorted(change.path for change in changes if _is_client_source(change.path))
    )
    client_tests = tuple(
        sorted(
            change.path
            for change in changes
            if _is_client_unit(change.path) or _is_client_e2e(change.path)
        )
    )
    if not (*python_source, *python_tests, *client_source, *client_tests):
        return AffectedPlan(
            "docs-only",
            "documentation and agent guidance only",
            tuple(sorted(c.path for c in changes)),
        )
    if python_source and client_source:
        return _comprehensive(
            changes,
            "mixed backend and client source changes cross an unmodelled contract boundary",
        )
    if len(python_source) + len(client_source) > MAX_CHANGED_SOURCE:
        return _comprehensive(
            changes, f"changed source count exceeds {MAX_CHANGED_SOURCE}"
        )

    try:
        python_plan = (
            _plan_python(python_source, python_tests)
            if (python_source or python_tests)
            else SurfacePlan()
        )
        client_plan = (
            _plan_client(client_source, client_tests)
            if (client_source or client_tests)
            else SurfacePlan()
        )
    except PlanError as exc:
        return _comprehensive(changes, str(exc))

    if (
        len(python_plan.impacted) > MAX_IMPACTED_SOURCE
        or len(client_plan.impacted) > MAX_IMPACTED_SOURCE
    ):
        return _comprehensive(
            changes,
            f"reverse dependency closure exceeds {MAX_IMPACTED_SOURCE}",
            python=python_plan,
            client=client_plan,
        )
    selected_count = sum(
        len(items)
        for items in (
            python_plan.unit_tests,
            python_plan.e2e_tests,
            client_plan.unit_tests,
            client_plan.e2e_tests,
        )
    )
    if selected_count > MAX_SELECTED_TESTS:
        return _comprehensive(
            changes,
            f"selected test count {selected_count} exceeds {MAX_SELECTED_TESTS}",
            python=python_plan,
            client=client_plan,
        )
    if python_plan.uncovered or client_plan.uncovered:
        return _comprehensive(
            changes,
            "reverse dependency closure contains source without graph-owned tests",
            python=python_plan,
            client=client_plan,
        )
    return AffectedPlan(
        scope="affected",
        reason="complete reverse dependency closure has graph-owned tests",
        changed_paths=tuple(sorted(change.path for change in changes)),
        python=python_plan,
        client=client_plan,
    )


def _write_multiline(output, name: str, values: Sequence[str]) -> None:
    output.write(f"{name}<<__AFFECTED__\n")
    output.write("\n".join(values))
    output.write("\n__AFFECTED__\n")


def write_github_output(path: Path, plan: AffectedPlan) -> None:
    if plan.lane("api_e2e") == "comprehensive":
        matrix = [{"shard": shard, "total": 4} for shard in range(1, 5)]
    elif plan.lane("api_e2e") == "affected":
        matrix = [{"shard": 1, "total": 1}]
    else:
        matrix = [{"shard": 0, "total": 0}]
    with path.open("a", encoding="utf-8") as output:
        output.write(f"scope={plan.scope}\n")
        for lane in (
            "api_quality",
            "api_unit",
            "api_e2e",
            "client_quality",
            "client_unit",
            "client_e2e",
            "mcp_conformance",
        ):
            output.write(f"{lane}_mode={plan.lane(lane)}\n")
        output.write(
            f"api_e2e_matrix={json.dumps({'include': matrix}, separators=(',', ':'))}\n"
        )
        _write_multiline(
            output,
            "api_quality_targets",
            tuple(
                sorted(
                    {
                        *(path.removeprefix("api/") for path in plan.python.impacted),
                        *plan.python.unit_tests,
                        *plan.python.e2e_tests,
                    }
                )
            ),
        )
        _write_multiline(output, "api_unit_targets", plan.python.unit_tests)
        _write_multiline(output, "api_e2e_targets", plan.python.e2e_tests)
        _write_multiline(
            output,
            "client_quality_targets",
            tuple(
                sorted(
                    {
                        *(
                            path.removeprefix("client/")
                            for path in plan.client.impacted
                        ),
                        *plan.client.unit_tests,
                        *plan.client.e2e_tests,
                    }
                )
            ),
        )
        _write_multiline(output, "client_unit_targets", plan.client.unit_tests)
        _write_multiline(output, "client_e2e_targets", plan.client.e2e_tests)


def write_summary(path: Path, plan: AffectedPlan) -> None:
    with path.open("a", encoding="utf-8") as summary:
        summary.write("## Affected test plan\n\n")
        summary.write(f"- Scope: `{plan.scope}`\n- Reason: {plan.reason}\n")
        summary.write(f"- Changed paths: {len(plan.changed_paths)}\n")
        summary.write(
            f"- Python impacted/tests: {len(plan.python.impacted)}/{len(plan.python.unit_tests) + len(plan.python.e2e_tests)}\n"
        )
        summary.write(
            f"- Client impacted/tests: {len(plan.client.impacted)}/{len(plan.client.unit_tests) + len(plan.client.e2e_tests)}\n"
        )
        summary.write(f"- Python runtime route edges: {plan.python.runtime_edges}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--force-comprehensive", action="store_true")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--plan-output", type=Path)
    parser.add_argument("--summary", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.force_comprehensive:
        plan = _comprehensive((), "event requires comprehensive validation")
    elif not args.base or not args.head:
        raise SystemExit(
            "--base and --head are required unless --force-comprehensive is used"
        )
    else:
        plan = plan_changes(git_changes(args.base, args.head))
    payload = json.dumps(plan.to_dict(), indent=2, sort_keys=True)
    print(payload)
    if args.github_output:
        write_github_output(args.github_output, plan)
    if args.plan_output:
        args.plan_output.write_text(payload + "\n", encoding="utf-8")
    if args.summary:
        write_summary(args.summary, plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
