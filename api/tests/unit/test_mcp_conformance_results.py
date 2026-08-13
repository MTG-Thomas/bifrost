"""Tests for official MCP conformance artifact validation."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from tests.conformance.summarize_results import main


def _write_checks(tmp_path, scenario: str, checks: list[dict]) -> None:
    result_dir = tmp_path / f"server-{scenario}-2026-08-13T00-00-00-000Z"
    result_dir.mkdir()
    (result_dir / "checks.json").write_text(json.dumps(checks))


def test_summary_accepts_non_empty_success_and_skip_results(tmp_path) -> None:
    _write_checks(
        tmp_path,
        "caching",
        [
            {"id": "cache-hints", "status": "SUCCESS"},
            {"id": "resource-read", "status": "SKIPPED"},
        ],
    )
    junit = tmp_path / "junit.xml"

    status = main(
        [
            "--results-dir",
            str(tmp_path),
            "--scenario",
            "caching",
            "--junit",
            str(junit),
        ]
    )

    suite = ET.parse(junit).getroot()
    assert status == 0
    assert suite.attrib == {
        "name": "mcp-conformance",
        "tests": "2",
        "failures": "0",
        "errors": "0",
        "skipped": "1",
    }


def test_summary_fails_closed_for_missing_scenario_and_writes_junit(tmp_path) -> None:
    junit = tmp_path / "junit.xml"

    status = main(
        [
            "--results-dir",
            str(tmp_path),
            "--scenario",
            "tools-list",
            "--junit",
            str(junit),
        ]
    )

    suite = ET.parse(junit).getroot()
    assert status == 1
    assert suite.attrib["tests"] == "1"
    assert suite.attrib["failures"] == "1"


def test_summary_fails_for_warning_or_failure_checks(tmp_path) -> None:
    _write_checks(
        tmp_path,
        "http-header-validation",
        [
            {"id": "valid", "status": "SUCCESS"},
            {"id": "should", "status": "WARNING", "errorMessage": "gap"},
        ],
    )
    junit = tmp_path / "junit.xml"

    status = main(
        [
            "--results-dir",
            str(tmp_path),
            "--scenario",
            "http-header-validation",
            "--junit",
            str(junit),
        ]
    )

    suite = ET.parse(junit).getroot()
    assert status == 1
    assert suite.attrib["tests"] == "2"
    assert suite.attrib["failures"] == "1"


def test_summary_fails_when_official_tools_list_does_not_match_gateway(tmp_path) -> None:
    _write_checks(
        tmp_path,
        "tools-list",
        [
            {
                "id": "tools-list",
                "status": "SUCCESS",
                "details": {"tools": ["unexpected_tool"]},
            }
        ],
    )
    junit = tmp_path / "junit.xml"

    status = main(
        [
            "--results-dir",
            str(tmp_path),
            "--scenario",
            "tools-list",
            "--junit",
            str(junit),
        ]
    )

    suite = ET.parse(junit).getroot()
    assert status == 1
    assert suite.attrib["tests"] == "2"
    assert suite.attrib["failures"] == "1"
