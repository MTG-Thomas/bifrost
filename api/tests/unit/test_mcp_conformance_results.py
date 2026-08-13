"""Tests for official MCP conformance artifact validation."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from tests.conformance.summarize_results import (
    EXPECTED_CHECK_PROFILES,
    EXPECTED_GATEWAY_TOOLS,
    main,
)


def _checks_for(scenario: str) -> list[dict]:
    checks = [
        {"id": check_id, "name": name, "status": status}
        for check_id, name, status in EXPECTED_CHECK_PROFILES[scenario]
    ]
    if scenario == "tools-list":
        checks[0]["details"] = {"tools": EXPECTED_GATEWAY_TOOLS}
    return checks


def _write_checks(tmp_path, scenario: str, checks: list[dict]) -> None:
    result_dir = tmp_path / f"server-{scenario}-2026-08-13T00-00-00-000Z"
    result_dir.mkdir()
    (result_dir / "checks.json").write_text(json.dumps(checks))


def _summarize(tmp_path, scenario: str) -> tuple[int, ET.Element]:
    junit = tmp_path / "junit.xml"
    status = main(
        [
            "--results-dir",
            str(tmp_path),
            "--scenario",
            scenario,
            "--junit",
            str(junit),
        ]
    )
    return status, ET.parse(junit).getroot()


def test_summary_accepts_exact_pinned_success_and_skip_profile(tmp_path) -> None:
    _write_checks(tmp_path, "caching", _checks_for("caching"))

    status, suite = _summarize(tmp_path, "caching")

    assert status == 0
    assert suite.attrib == {
        "name": "mcp-conformance",
        "tests": "8",
        "failures": "0",
        "errors": "0",
        "skipped": "1",
    }


def test_summary_fails_closed_for_missing_scenario_and_writes_junit(tmp_path) -> None:
    status, suite = _summarize(tmp_path, "tools-list")

    assert status == 1
    assert suite.attrib["tests"] == "1"
    assert suite.attrib["failures"] == "1"


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "new-skip"])
def test_summary_rejects_any_change_to_pinned_check_profile(tmp_path, change) -> None:
    checks = _checks_for("caching")
    if change == "missing":
        checks.pop(0)
    elif change == "extra":
        checks.append({"id": "new-check", "name": "NewCheck", "status": "SUCCESS"})
    elif change == "duplicate":
        checks.append(dict(checks[0]))
    else:
        checks[0]["status"] = "SKIPPED"
    _write_checks(tmp_path, "caching", checks)

    status, suite = _summarize(tmp_path, "caching")

    assert status == 1
    assert int(suite.attrib["failures"]) >= 1


def test_summary_fails_for_warning_check(tmp_path) -> None:
    checks = _checks_for("http-header-validation")
    checks[0]["status"] = "WARNING"
    checks[0]["errorMessage"] = "gap"
    _write_checks(tmp_path, "http-header-validation", checks)

    status, suite = _summarize(tmp_path, "http-header-validation")

    assert status == 1
    assert int(suite.attrib["failures"]) >= 1


@pytest.mark.parametrize(
    "observed",
    [
        ["unexpected_tool"],
        [*EXPECTED_GATEWAY_TOOLS, EXPECTED_GATEWAY_TOOLS[0]],
        list(reversed(EXPECTED_GATEWAY_TOOLS)),
    ],
)
def test_summary_requires_exact_ordered_four_tool_gateway(tmp_path, observed) -> None:
    checks = _checks_for("tools-list")
    checks[0]["details"] = {"tools": observed}
    _write_checks(tmp_path, "tools-list", checks)

    status, suite = _summarize(tmp_path, "tools-list")

    assert status == 1
    assert suite.attrib["tests"] == "4"
    assert suite.attrib["failures"] == "1"
