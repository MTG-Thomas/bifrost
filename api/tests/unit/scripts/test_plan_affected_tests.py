from __future__ import annotations

from pathlib import Path

import pytest

from scripts import plan_affected_tests as affected


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def graph_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(affected, "REPO_ROOT", tmp_path)
    return tmp_path


def test_backend_helper_selects_reverse_importers_and_route_e2e(
    graph_repo: Path,
) -> None:
    _write(
        graph_repo,
        "api/src/services/helper.py",
        "def normalize(value):\n    return value\n",
    )
    _write(
        graph_repo,
        "api/src/services/report.py",
        "from src.services.helper import normalize\n\ndef report(value):\n    return normalize(value)\n",
    )
    _write(
        graph_repo,
        "api/src/routers/reports.py",
        "from fastapi import APIRouter\nfrom src.services.report import report\n"
        "router = APIRouter(prefix='/api/reports')\n"
        "@router.get('/{report_id}')\ndef get_report(report_id):\n    return report(report_id)\n",
    )
    _write(
        graph_repo,
        "api/tests/unit/services/test_report.py",
        "from src.services.report import report\n\ndef test_report():\n    assert report(1) == 1\n",
    )
    _write(
        graph_repo,
        "api/tests/unit/routers/test_reports.py",
        "from src.routers.reports import get_report\n\ndef test_route():\n    assert get_report(1) == 1\n",
    )
    _write(
        graph_repo,
        "api/tests/e2e/api/test_reports.py",
        "def test_report(e2e_client):\n    assert e2e_client.get('/api/reports/123')\n",
    )

    plan = affected.plan_changes(
        [affected.GitChange("M", "api/src/services/helper.py")]
    )

    assert plan.scope == "affected"
    assert plan.python.impacted == (
        "api/src/routers/reports.py",
        "api/src/services/helper.py",
        "api/src/services/report.py",
    )
    assert plan.python.unit_tests == (
        "tests/unit/routers/test_reports.py",
        "tests/unit/services/test_report.py",
    )
    assert plan.python.e2e_tests == ("tests/e2e/api/test_reports.py",)
    assert plan.python.runtime_edges == 1


def test_unowned_backend_downstream_falls_back_to_comprehensive(
    graph_repo: Path,
) -> None:
    _write(graph_repo, "api/src/services/helper.py", "VALUE = 1\n")
    _write(
        graph_repo,
        "api/src/services/unowned.py",
        "from src.services.helper import VALUE\n",
    )
    _write(
        graph_repo,
        "api/tests/unit/services/test_helper.py",
        "from src.services.helper import VALUE\n\ndef test_value():\n    assert VALUE == 1\n",
    )

    plan = affected.plan_changes(
        [affected.GitChange("M", "api/src/services/helper.py")]
    )

    assert plan.scope == "comprehensive"
    assert plan.python.uncovered == ("api/src/services/unowned.py",)


def test_client_helper_selects_transitive_component_test(graph_repo: Path) -> None:
    _write(
        graph_repo,
        "client/src/lib/format.ts",
        "export const format = (value: string) => value;\n",
    )
    _write(
        graph_repo,
        "client/src/components/Value.tsx",
        "import { format } from '@/lib/format';\nexport const Value = () => format('ok');\n",
    )
    _write(
        graph_repo,
        "client/src/components/Value.test.tsx",
        "import { Value } from './Value';\nit('works', () => expect(Value()).toBe('ok'));\n",
    )

    plan = affected.plan_changes([affected.GitChange("M", "client/src/lib/format.ts")])

    assert plan.scope == "affected"
    assert plan.client.impacted == (
        "client/src/components/Value.tsx",
        "client/src/lib/format.ts",
    )
    assert plan.client.unit_tests == ("src/components/Value.test.tsx",)
    assert plan.lane("client_e2e") == "skip"


def test_unowned_client_page_requires_comprehensive_browser_validation(
    graph_repo: Path,
) -> None:
    _write(
        graph_repo,
        "client/src/pages/Reports.tsx",
        "export const Reports = () => null;\n",
    )
    _write(
        graph_repo,
        "client/src/pages/Reports.test.tsx",
        "import { Reports } from './Reports';\nit('loads', () => expect(Reports()).toBeNull());\n",
    )

    plan = affected.plan_changes(
        [affected.GitChange("M", "client/src/pages/Reports.tsx")]
    )

    assert plan.scope == "comprehensive"
    assert plan.client.uncovered == ("client/src/pages/Reports.tsx",)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (affected.GitChange("D", "api/src/services/old.py"), "deletions"),
        (affected.GitChange("M", "requirements.lock"), "high-risk"),
        (affected.GitChange("M", "unexpected.toml"), "unmodelled"),
    ],
)
def test_uncertain_changes_fail_closed(
    graph_repo: Path,
    change: affected.GitChange,
    reason: str,
) -> None:
    plan = affected.plan_changes([change])

    assert plan.scope == "comprehensive"
    assert reason in plan.reason


def test_python_test_only_change_runs_exact_test(graph_repo: Path) -> None:
    _write(
        graph_repo,
        "api/tests/unit/services/test_leaf.py",
        "def test_leaf():\n    assert True\n",
    )

    plan = affected.plan_changes(
        [affected.GitChange("M", "api/tests/unit/services/test_leaf.py")]
    )

    assert plan.scope == "affected"
    assert plan.python.unit_tests == ("tests/unit/services/test_leaf.py",)
    assert plan.lane("api_unit") == "affected"
    assert plan.lane("api_e2e") == "skip"


def test_git_changes_rejects_argument_injection() -> None:
    with pytest.raises(affected.PlanError, match="full Git commit SHAs"):
        affected.git_changes("--output=/tmp/escaped", "a" * 40)


def test_ci_evidence_paths_must_match_runner_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(allowed))

    assert affected._validated_ci_output(allowed, "GITHUB_OUTPUT") == allowed.resolve()
    with pytest.raises(SystemExit, match="refusing to write"):
        affected._validated_ci_output(tmp_path / "escaped", "GITHUB_OUTPUT")
