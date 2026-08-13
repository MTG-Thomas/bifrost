#!/usr/bin/env python3
"""Guard MTG fork CI ownership boundaries."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


EXPECTED_DEPLOY_DEV_IF = (
    "github.repository == 'gobifrost/bifrost' "
    "&& github.event_name == 'push' "
    "&& github.ref == 'refs/heads/main'"
)
EXPECTED_DEPLOY_DRY_RUN_IF = (
    "github.repository == 'gobifrost/bifrost' "
    "&& github.event_name == 'workflow_dispatch'"
)
REQUIRED_CI_JOB_NAMES = {
    "lint": "Lint & Type Check",
    "test-client-unit": "Client Unit Tests",
    "test-unit": "Unit Tests",
    "mcp-conformance": "MCP Conformance",
    "test-e2e-gate": "E2E Tests",
}


def _repo_root() -> Path:
    path = Path(__file__).resolve()
    root = path.parents[2]
    if root == root.parent:
        # The API test container mounts api/ at /app, so api/scripts becomes
        # /app/scripts and parents[2] is the filesystem root.
        return path.parents[1]
    return root


def _resolve_workflow_path(path: Path) -> Path:
    root = _repo_root()
    resolved = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{path}: workflow path must stay within {root}")
    return resolved


def _job_block(lines: list[str], job_name: str) -> tuple[int, list[str]] | None:
    marker = f"  {job_name}:"
    for index, line in enumerate(lines):
        if line == marker:
            block: list[str] = []
            for block_line in lines[index + 1 :]:
                if block_line.startswith("  ") and not block_line.startswith("    "):
                    break
                block.append(block_line)
            return index + 1, block
    return None


def _parse_if_line(block: list[str]) -> str | None:
    for line in block:
        stripped = line.strip()
        if stripped.startswith("if:"):
            return stripped.removeprefix("if:").strip()
    return None


def _parse_name_line(block: list[str]) -> str | None:
    for line in block:
        stripped = line.strip()
        if stripped.startswith("name:"):
            return stripped.removeprefix("name:").strip().strip("\"'")
    return None


def check_ci_workflow(path: Path) -> list[str]:
    if not path.exists():
        return [f"{path}: workflow file does not exist"]

    lines = path.read_text(encoding="utf-8").splitlines()
    for job, expected_name in REQUIRED_CI_JOB_NAMES.items():
        parsed = _job_block(lines, job)
        if parsed is None:
            return [f"{path}: required CI job {job!r} is missing"]
        start_line, block = parsed
        actual_name = _parse_name_line(block)
        if actual_name != expected_name:
            return [
                f"{path}:{start_line}: required CI check identity changed for {job!r}.",
                f"expected: name: {expected_name}",
                f"actual:   name: {actual_name or '<missing>'}",
                "Update repository rules first; never silently orphan a required check.",
            ]

    deploy_dry_run = _job_block(lines, "deploy-dry-run")
    if deploy_dry_run is None:
        return [
            f"{path}: deploy-dry-run job is missing; remove the MTG boundary guard "
            "only if the upstream DigitalOcean dry-run lane has been deleted intentionally."
        ]

    dry_run_start_line, dry_run_block = deploy_dry_run
    dry_run_if = _parse_if_line(dry_run_block)
    if dry_run_if != EXPECTED_DEPLOY_DRY_RUN_IF:
        return [
            f"{path}:{dry_run_start_line}: deploy-dry-run must stay upstream-only for MTG-Thomas/bifrost.",
            f"expected: if: {EXPECTED_DEPLOY_DRY_RUN_IF}",
            f"actual:   if: {dry_run_if or '<missing>'}",
            "MTG has no DigitalOcean deploy credentials; validate Azure deployments from bifrost-infra.",
        ]

    deploy_dev = _job_block(lines, "deploy-dev")
    if deploy_dev is None:
        return [
            f"{path}: deploy-dev job is missing; remove the MTG boundary guard "
            "only if the upstream DigitalOcean deploy lane has been deleted intentionally."
        ]

    start_line, block = deploy_dev
    if_condition = _parse_if_line(block)
    if if_condition != EXPECTED_DEPLOY_DEV_IF:
        return [
            f"{path}:{start_line}: deploy-dev must stay upstream-only for MTG-Thomas/bifrost.",
            f"expected: if: {EXPECTED_DEPLOY_DEV_IF}",
            f"actual:   if: {if_condition or '<missing>'}",
            "MTG deploys from bifrost-infra to Azure; do not re-enable DigitalOcean CI/CD in this fork.",
        ]

    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check MTG fork CI boundaries that must survive upstream merges."
    )
    parser.add_argument(
        "--workflow",
        type=Path,
        default=Path(".github/workflows/ci.yml"),
        help="CI workflow to inspect.",
    )
    args = parser.parse_args(argv)

    try:
        workflow = _resolve_workflow_path(args.workflow)
    except ValueError as exc:
        print(f"MTG CI boundary check failed: {exc}", file=sys.stderr)
        return 2

    violations = check_ci_workflow(workflow)
    if not violations:
        print("MTG CI boundary checks passed.")
        return 0

    print("MTG CI boundary check failed:", file=sys.stderr)
    for violation in violations:
        print(f"- {violation}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
