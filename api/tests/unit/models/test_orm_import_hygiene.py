from __future__ import annotations

import ast
from pathlib import Path


ORM_DIR = Path(__file__).parents[3] / "src" / "models" / "orm"

CODEQL_UNSAFE_CYCLIC_IMPORT_MODULES = [
    "agent_runs.py",
    "agents.py",
    "ai_usage.py",
    "applications.py",
    "cli.py",
    "config.py",
    "executions.py",
    "forms.py",
    "integrations.py",
    "knowledge.py",
    "mfa.py",
    "oauth.py",
    "organizations.py",
    "tables.py",
    "workflow_roles.py",
    "workflows.py",
]
CODEQL_UNSAFE_CYCLIC_IMPORT_NAMES = {
    f"src.models.orm.{Path(filename).stem}"
    for filename in CODEQL_UNSAFE_CYCLIC_IMPORT_MODULES
}


def test_codeql_cyclic_import_cluster_has_no_runtime_peer_imports() -> None:
    """ORM peers may be imported for typing, but never during module execution."""

    for filename in CODEQL_UNSAFE_CYCLIC_IMPORT_MODULES:
        source = (ORM_DIR / filename).read_text(encoding="utf-8")
        tree = ast.parse(source)
        runtime_imports: list[str] = []
        for node in tree.body:
            if (
                isinstance(node, ast.ImportFrom)
                and (node.module or "") in CODEQL_UNSAFE_CYCLIC_IMPORT_NAMES
            ):
                runtime_imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                runtime_imports.extend(
                    alias.name
                    for alias in node.names
                    if alias.name in CODEQL_UNSAFE_CYCLIC_IMPORT_NAMES
                )

        assert runtime_imports == [], f"{filename} imports ORM peers at runtime"


def test_orm_package_exports_still_import() -> None:
    from src.models.orm import AgentRun, AIUsage, Organization, User, Workflow

    assert AgentRun.__tablename__ == "agent_runs"
    assert AIUsage.__tablename__ == "ai_usage"
    assert Organization.__tablename__ == "organizations"
    assert User.__tablename__ == "users"
    assert Workflow.__tablename__ == "workflows"
