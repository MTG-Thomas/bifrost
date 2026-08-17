"""Contract tests for the exact-pinned official MCP conformance harness."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path.cwd()
CONFORMANCE_ROOT = API_ROOT / "tests" / "conformance"
RUNNER_VERSION = "0.2.0-alpha.11"
NODE_IMAGE = (
    "node:22.18.0-bookworm-slim@"
    "sha256:752ea8a2f758c34002a0461bd9f1cee4f9a3c36d48494586f60ffce1fc708e0e"
)


def test_official_runner_and_lockfile_are_exact_pinned() -> None:
    package = json.loads((CONFORMANCE_ROOT / "package.json").read_text())
    lock = json.loads((CONFORMANCE_ROOT / "package-lock.json").read_text())

    assert (
        package["dependencies"]["@modelcontextprotocol/conformance"] == RUNNER_VERSION
    )
    assert (
        lock["packages"][""]["dependencies"]["@modelcontextprotocol/conformance"]
        == RUNNER_VERSION
    )
    assert (
        lock["packages"]["node_modules/@modelcontextprotocol/conformance"]["version"]
        == RUNNER_VERSION
    )
    dockerfile = (CONFORMANCE_ROOT / "Dockerfile").read_text()
    assert dockerfile.splitlines()[0] == f"FROM {NODE_IMAGE}"
    assert "\nUSER node\n" in dockerfile


def test_compose_and_test_runner_use_the_pinned_official_image() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.test.yml").read_text())
    service = compose["services"]["mcp-conformance"]
    adapter = compose["services"]["mcp-conformance-adapter"]

    assert service["image"] == f"bifrost-test-mcp-conformance:{RUNNER_VERSION}"
    assert service["build"] == {
        "context": "./api/tests/conformance",
        "dockerfile": "Dockerfile",
    }
    assert service["depends_on"] == {
        "mcp-conformance-adapter": {"condition": "service_healthy"}
    }
    assert adapter["image"] == "bifrost-test-api-dev:latest"
    assert adapter["volumes"] == ["./api/tests:/app/tests:ro"]
    assert "ports" not in adapter
    assert adapter["depends_on"] == {"api": {"condition": "service_healthy"}}
    assert adapter["security_opt"] == ["no-new-privileges:true"]

    test_script = (REPO_ROOT / "test.sh").read_text()
    assert "mcp) shift; cmd_mcp" in test_script
    assert "--url http://mcp-conformance-adapter:8080/mcp" in test_script
    assert "--spec-version 2026-07-28" in test_script
    assert "--expected-failures" not in test_script
    assert "auth-probe-headers.txt" in test_script
    assert "protected-resource-metadata.json" in test_script
    assert "^HTTP/1.1 401 Unauthorized$" in test_script
    assert "^www-authenticate: Bearer .*resource_metadata=" in test_script
    assert "grep -q '\"mcp:access\"'" in test_script
    conformance_function = test_script.split("mcp_conformance() {", 1)[1].split(
        "\n}\n", 1
    )[0]
    assert "chmod 777" not in conformance_function
    assert '--user "$(id -u):$(id -g)" mcp-conformance' in test_script
    assert "summarize_results.py" in conformance_function
    assert "conformance-junit.xml" in conformance_function


def test_blocking_and_advisory_scenarios_have_no_expected_failure_file() -> None:
    assert not (CONFORMANCE_ROOT / "expected-failures.yml").exists()
    readme = (CONFORMANCE_ROOT / "README.md").read_text()
    assert "without an\nexpected-failures file" in readme
    assert "tools-list" in readme
    assert "caching" in readme
    assert "http-header-validation" in readme
    assert "23/25 checks pass" in readme
    assert "401 Unauthorized" in readme


def test_ci_job_blocks_and_retains_runner_artifacts() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["mcp-conformance"]

    assert not job.get("continue-on-error", False)
    assert job["name"] == "MCP Conformance"
    steps = {step["name"]: step for step in job["steps"]}
    assert steps["Checkout repository"]["with"]["persist-credentials"] is False
    step_names = list(steps)
    assert step_names.index("Detect test image input changes") < step_names.index(
        "Prepare CI test images"
    )
    assert step_names.index("Prepare CI test images") < step_names.index(
        "Run blocking MCP conformance"
    )
    assert (
        steps["Prepare CI test images"]["run"]
        == "bash api/scripts/ci/prepare-test-images.sh api client"
    )
    assert "./test.sh mcp conformance" in steps["Run blocking MCP conformance"]["run"]
    artifact = steps["Upload MCP conformance results"]
    assert artifact["if"] == (
        "always() && needs.affected-test-plan.outputs.mcp_conformance_mode != 'skip'"
    )
    assert artifact["with"]["path"] == "/tmp/bifrost-*/mcp-conformance/"
    assert artifact["with"]["if-no-files-found"] == "error"
    assert "mcp-conformance" in workflow["jobs"]["verify-release-manifest"]["needs"]
    e2e_gate = workflow["jobs"]["test-e2e-gate"]
    assert "mcp-conformance" in e2e_gate["needs"]
    gate_script = e2e_gate["steps"][0]["run"]
    assert "needs.mcp-conformance.result" in gate_script
    assert "MCP conformance failed or was cancelled" in gate_script
