"""Contract tests for the exact-pinned official MCP conformance harness."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = API_ROOT.parent if (API_ROOT.parent / "test.sh").exists() else API_ROOT
CONFORMANCE_ROOT = API_ROOT / "tests" / "conformance"
RUNNER_VERSION = "0.2.0-alpha.11"
NODE_IMAGE = (
    "node:22.18.0-bookworm-slim@"
    "sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e"
)


def test_official_runner_and_lockfile_are_exact_pinned() -> None:
    package = json.loads((CONFORMANCE_ROOT / "package.json").read_text())
    lock = json.loads((CONFORMANCE_ROOT / "package-lock.json").read_text())

    assert package["dependencies"]["@modelcontextprotocol/conformance"] == RUNNER_VERSION
    assert lock["packages"][""]["dependencies"][
        "@modelcontextprotocol/conformance"
    ] == RUNNER_VERSION
    assert lock["packages"][
        "node_modules/@modelcontextprotocol/conformance"
    ]["version"] == RUNNER_VERSION
    assert (CONFORMANCE_ROOT / "Dockerfile").read_text().splitlines()[0] == (
        f"FROM {NODE_IMAGE}"
    )


def test_compose_and_test_runner_use_the_pinned_official_image() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.test.yml").read_text())
    service = compose["services"]["mcp-conformance"]

    assert service["image"] == f"bifrost-test-mcp-conformance:{RUNNER_VERSION}"
    assert service["build"] == {
        "context": "./api/tests/conformance",
        "dockerfile": "Dockerfile",
    }

    test_script = (REPO_ROOT / "test.sh").read_text()
    assert "mcp) shift; cmd_mcp" in test_script
    assert "--url http://api:8000/mcp" in test_script
    assert "--scenario server-initialize" in test_script
    assert "--spec-version 2025-11-25" in test_script
    assert "auth-probe-headers.txt" in test_script
    assert "^HTTP/1.1 401 Unauthorized$" in test_script
    assert 'scope="mcp:access"' in test_script


def test_advisory_baseline_only_records_auth_blocked_initialize() -> None:
    baseline = yaml.safe_load(
        (CONFORMANCE_ROOT / "expected-failures.yml").read_text()
    )

    assert baseline == {"server": ["server-initialize"]}

    baseline_text = (CONFORMANCE_ROOT / "expected-failures.yml").read_text()
    readme = (CONFORMANCE_ROOT / "README.md").read_text()
    assert "401 Bearer challenge" in baseline_text
    assert "401 Unauthorized" in readme
    assert "cannot mount" not in readme
    assert "challenge_scopes" not in readme


def test_ci_job_is_advisory_and_retains_runner_artifacts() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["mcp-conformance"]

    assert job["continue-on-error"] is True
    steps = {step["name"]: step for step in job["steps"]}
    assert "./test.sh mcp conformance" in steps[
        "Run advisory MCP conformance"
    ]["run"]
    artifact = steps["Upload MCP conformance results"]
    assert artifact["if"] == "always()"
    assert artifact["with"]["path"] == "/tmp/bifrost-*/mcp-conformance/"
