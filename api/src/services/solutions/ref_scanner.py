"""Static reference scanners for the Solution capture/export dependency walker.

These find the *string* references a workspace file makes to other entities, so
the dependency walker can compute what a capture/export selection pulls in (and,
in reverse, what outside it still points at the selection).

The scans are STATIC and intentionally simple:
- Python module imports reuse the canonical AST scanner in
  ``solution_vendoring`` (workflows -> ``modules/*.py``).
- Entity name/path references (``tables.get("x")``, ``config.get("k")``,
  ``useWorkflow("p::f")``, ``workflows.execute("p::f")``,
  ``useTable("x")``, ``integrations.get("Name")``) are
  matched as STRING LITERALS. Python ``workflows.execute`` calls use the AST
  so comments and docstrings are ignored; the other references use regex so
  this scanner also works for non-Python sources. Dynamic references built
  from variables are invisible — which is exactly why the capture/export
  preview is a deselectable human-checked list (capture-design §3.3), not an
  automatic gate.

Using the AST only for Python execution calls keeps one scanner working across
both Python and TSX without a TS parser, at the cost of missing computed refs.
That trade-off is the documented design: the human is the authority over the
preview.
"""

from __future__ import annotations

import ast
import re

from bifrost.solution_vendoring import scan_imported_modules

__all__ = [
    "scan_imported_modules",
    "scan_table_refs",
    "scan_config_refs",
    "scan_workflow_refs",
    "scan_integration_refs",
]

# A quoted string literal, single or double quotes, capturing the inner value.
_STR = r"""['"]([^'"]+)['"]"""

# ``tables.get("name")`` / ``sdk.tables.get("name")`` / ``useTable("name")``.
# The first arg is the table name (``tables.get`` may take a row id as 2nd arg,
# which we ignore). Leading ``sdk.`` is optional.
_TABLE_RE = re.compile(
    rf"""(?:\buseTable\s*\(\s*|\btables\s*\.\s*get\s*\(\s*){_STR}"""
)

# ``config.get("key")`` / ``sdk.config.get("key")``.
_CONFIG_RE = re.compile(rf"""\bconfig\s*\.\s*get\s*\(\s*{_STR}""")

# App workflow hooks (TSX): ``useWorkflow`` / ``useWorkflowQuery`` /
# ``useWorkflowMutation``. The first arg is a workflow IDENTIFIER — either a
# bare name (``'get_clients'``) or a portable ``path::function`` ref — so the
# walker resolves the captured value both ways. ``(?:Query|Mutation)?`` keeps
# the three hook names in one pattern.
_WORKFLOW_RE = re.compile(
    rf"""\buseWorkflow(?:Query|Mutation)?\s*\(\s*{_STR}"""
)

# Python workflow execution calls (``workflows.execute("path::fn")``).
_WORKFLOW_EXECUTE_RE = re.compile(
    rf"""(?<![\w.])(?:sdk\.)?workflows\.execute\s*\(\s*{_STR}"""
)


# ``integrations.get("Name")`` / ``sdk.integrations.get("Name")``.
# First arg is the integration NAME (a string literal). Dynamic refs are
# invisible — same documented static-scan tradeoff as configs/tables.
_INTEGRATION_RE = re.compile(
    rf"""\bintegrations\s*\.\s*get\s*\(\s*{_STR}"""
)


def scan_table_refs(source: str) -> set[str]:
    """Return table NAMES referenced by ``source`` (``tables.get``/``useTable``)."""
    return set(_TABLE_RE.findall(source))


def scan_config_refs(source: str) -> set[str]:
    """Return config KEYS referenced by ``source`` (``config.get``)."""
    return set(_CONFIG_RE.findall(source))


def scan_workflow_refs(source: str) -> set[str]:
    """Return workflow identifiers in ``source``.

    Matches ``useWorkflow``/``useWorkflowQuery``/``useWorkflowMutation`` and
    ``workflows.execute``; the captured value is a bare name OR a ``path::fn``
    ref (caller resolves both).
    """
    execute_refs = _scan_python_workflow_execute_refs(source)
    if execute_refs is None:
        # Non-Python sources (currently TSX) still use the shared text scanner.
        execute_refs = set(_WORKFLOW_EXECUTE_RE.findall(source))
    return set(_WORKFLOW_RE.findall(source)) | execute_refs


def _scan_python_workflow_execute_refs(source: str) -> set[str] | None:
    """Scan valid Python for literal ``workflows.execute`` calls.

    Returning ``None`` for a syntax error lets callers retain regex scanning
    for non-Python sources such as TSX.  AST traversal intentionally ignores
    comments and docstrings, while only accepting the two SDK spellings that
    the regex scanner recognizes (``workflows.execute`` and
    ``sdk.workflows.execute``).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    refs: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "execute":
            continue
        target = func.value
        direct_sdk = isinstance(target, ast.Name) and target.id == "workflows"
        namespaced_sdk = (
            isinstance(target, ast.Attribute)
            and target.attr == "workflows"
            and isinstance(target.value, ast.Name)
            and target.value.id == "sdk"
        )
        if not (direct_sdk or namespaced_sdk):
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            refs.add(argument.value)
    return refs


def scan_integration_refs(source: str) -> set[str]:
    """Return integration NAMES referenced via ``integrations.get(...)``."""
    return set(_INTEGRATION_RE.findall(source))
