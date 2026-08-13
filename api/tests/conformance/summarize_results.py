"""Validate official MCP result artifacts and emit a small JUnit report."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


EXPECTED_GATEWAY_TOOLS = [
    "bifrost_find_agents",
    "bifrost_get_agent",
    "bifrost_get_tool_schema",
    "bifrost_execute_tool",
]

EXPECTED_CHECK_PROFILES = {
    "tools-list": [
        ("tools-list", "ToolsList", "SUCCESS"),
        ("tools-name-format", "ToolsNameFormat", "SUCCESS"),
        ("wire-schema-valid", "WireSchemaValid", "SUCCESS"),
    ],
    "caching": [
        ("sep-2549-tools-list-caching-hints", "ToolsListCachingHints", "SUCCESS"),
        ("sep-2549-prompts-list-caching-hints", "PromptsListCachingHints", "SUCCESS"),
        (
            "sep-2549-resources-list-caching-hints",
            "ResourcesListCachingHints",
            "SUCCESS",
        ),
        (
            "sep-2549-resources-templates-list-caching-hints",
            "ResourcesTemplatesListCachingHints",
            "SUCCESS",
        ),
        (
            "sep-2549-resources-read-caching-hints",
            "ResourcesReadCachingHints",
            "SKIPPED",
        ),
        ("sep-2549-ttl-non-negative", "TtlNonNegative", "SUCCESS"),
        ("sep-2549-cache-scope-valid", "CacheScopeValid", "SUCCESS"),
        ("wire-schema-valid", "WireSchemaValid", "SUCCESS"),
    ],
    "http-header-validation": [
        (
            "sep-2243-server-reject-invalid-headers",
            "ServerRejectsMismatchedMethodHeader",
            "SUCCESS",
        ),
        (
            "sep-2243-server-reject-error-code",
            "ServerRejectsMismatchedMethodHeaderErrorCode",
            "SUCCESS",
        ),
        (
            "sep-2243-server-reject-invalid-headers",
            "ServerRejectsMissingMethodHeader",
            "SUCCESS",
        ),
        (
            "sep-2243-server-reject-error-code",
            "ServerRejectsMissingMethodHeaderErrorCode",
            "SUCCESS",
        ),
        (
            "sep-2243-server-reject-invalid-headers",
            "ServerRejectsMismatchedNameHeader",
            "SUCCESS",
        ),
        (
            "sep-2243-server-reject-error-code",
            "ServerRejectsMismatchedNameHeaderErrorCode",
            "SUCCESS",
        ),
        (
            "sep-2243-server-accepts-whitespace-header-value",
            "ServerAcceptsWhitespaceHeaderValue",
            "SUCCESS",
        ),
        (
            "sep-2243-server-reject-invalid-headers",
            "ServerRejectsMissingNameHeader",
            "SUCCESS",
        ),
        (
            "sep-2243-server-reject-error-code",
            "ServerRejectsMissingNameHeaderErrorCode",
            "SUCCESS",
        ),
        (
            "sep-2243-header-name-case-insensitive",
            "ServerAcceptsLowercaseHeaderName",
            "SUCCESS",
        ),
        (
            "sep-2243-header-name-case-insensitive",
            "ServerAcceptsUppercaseHeaderName",
            "SUCCESS",
        ),
        (
            "sep-2243-server-reject-invalid-headers",
            "ServerRejectsCaseMismatchValue",
            "SUCCESS",
        ),
        (
            "sep-2243-server-reject-error-code",
            "ServerRejectsCaseMismatchValueErrorCode",
            "SUCCESS",
        ),
        ("wire-schema-valid", "WireSchemaValid", "SUCCESS"),
    ],
}


def _scenario_directory(results_dir: Path, scenario: str) -> Path:
    matches = sorted(results_dir.glob(f"server-{scenario}-*"))
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one result directory for {scenario!r}, "
            f"found {len(matches)}"
        )
    return matches[0]


def load_blocking_results(
    results_dir: Path, scenarios: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Load non-empty official check arrays for every expected scenario."""
    loaded: dict[str, list[dict[str, Any]]] = {}
    for scenario in scenarios:
        checks_path = _scenario_directory(results_dir, scenario) / "checks.json"
        try:
            checks = json.loads(checks_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"could not load {checks_path}: {exc}") from exc
        if not isinstance(checks, list) or not checks:
            raise ValueError(f"{checks_path} contains no conformance checks")
        if not all(isinstance(check, dict) for check in checks):
            raise ValueError(f"{checks_path} is not an array of check objects")
        if not any(check.get("status") == "SUCCESS" for check in checks):
            raise ValueError(f"{checks_path} contains no successful checks")
        loaded[scenario] = checks
    return loaded


def validate_check_profiles(
    results: dict[str, list[dict[str, Any]]], scenarios: list[str]
) -> list[str]:
    """Require the exact reviewed check identities and statuses for the pin."""
    errors: list[str] = []
    for scenario in scenarios:
        expected = EXPECTED_CHECK_PROFILES.get(scenario)
        if expected is None:
            errors.append(f"no pinned check profile exists for {scenario!r}")
            continue
        checks = results.get(scenario)
        if checks is None:
            continue
        observed = [
            (check.get("id"), check.get("name"), check.get("status"))
            for check in checks
        ]
        if observed != expected:
            errors.append(
                f"official {scenario!r} check profile changed: "
                f"expected {expected!r}, observed {observed!r}"
            )
    return errors


def write_junit(
    path: Path,
    scenarios: list[str],
    results: dict[str, list[dict[str, Any]]],
    errors: list[str],
) -> None:
    """Write failures, warnings, skips, and harness errors as JUnit cases."""
    suite = ET.Element("testsuite", name="mcp-conformance")
    failures = 0
    skipped = 0
    test_count = 0

    for scenario in scenarios:
        for check in results.get(scenario, []):
            test_count += 1
            case = ET.SubElement(
                suite,
                "testcase",
                classname=f"mcp.conformance.{scenario}",
                name=str(check.get("id") or check.get("name") or "unnamed-check"),
            )
            status = check.get("status")
            if status == "SKIPPED":
                skipped += 1
                ET.SubElement(case, "skipped")
            elif status != "SUCCESS":
                failures += 1
                failure = ET.SubElement(
                    case,
                    "failure",
                    message=str(check.get("errorMessage") or f"status={status}"),
                )
                failure.text = json.dumps(check, indent=2, sort_keys=True)

    for index, message in enumerate(errors, start=1):
        test_count += 1
        failures += 1
        case = ET.SubElement(
            suite,
            "testcase",
            classname="mcp.conformance.harness",
            name=f"artifact-validation-{index}",
        )
        ET.SubElement(case, "failure", message=message)

    suite.set("tests", str(test_count))
    suite.set("failures", str(failures))
    suite.set("errors", "0")
    suite.set("skipped", str(skipped))
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--scenario", action="append", dest="scenarios", required=True)
    parser.add_argument("--junit", type=Path, required=True)
    args = parser.parse_args(argv)

    errors: list[str] = []
    results: dict[str, list[dict[str, Any]]] = {}
    for scenario in args.scenarios:
        try:
            results.update(load_blocking_results(args.results_dir, [scenario]))
        except ValueError as exc:
            errors.append(str(exc))

    errors.extend(validate_check_profiles(results, args.scenarios))

    tools_list_checks = results.get("tools-list")
    if tools_list_checks is not None:
        tools_list = next(
            (check for check in tools_list_checks if check.get("id") == "tools-list"),
            None,
        )
        observed = (
            tools_list.get("details", {}).get("tools")
            if tools_list is not None
            else None
        )
        if (
            not isinstance(observed, list)
            or observed != EXPECTED_GATEWAY_TOOLS
        ):
            errors.append(
                "official tools-list artifact did not report Bifrost's exact "
                f"four-tool gateway: {observed!r}"
            )

    failed_checks: list[str] = []
    for scenario, checks in results.items():
        for check in checks:
            if check.get("status") not in {"SUCCESS", "SKIPPED"}:
                failed_checks.append(
                    f"{scenario}:{check.get('id', 'unnamed-check')} "
                    f"reported {check.get('status')}"
                )

    write_junit(args.junit, args.scenarios, results, errors)
    if errors or failed_checks:
        for error in [*errors, *failed_checks]:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    total = sum(len(checks) for checks in results.values())
    print(f"Validated {total} official checks across {len(results)} scenarios.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
