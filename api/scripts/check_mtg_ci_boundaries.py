#!/usr/bin/env python3
"""Guard MTG fork CI ownership boundaries."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


EXPECTED_DEPLOY_DEV_IF = (
    "github.repository == 'jackmusick/bifrost' "
    "&& github.event_name == 'push' "
    "&& github.ref == 'refs/heads/main'"
)


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


def check_ci_workflow(path: Path) -> list[str]:
    if not path.exists():
        return [f"{path}: workflow file does not exist"]

    lines = path.read_text(encoding="utf-8").splitlines()
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

    violations = check_ci_workflow(args.workflow)
    if not violations:
        print("MTG CI boundary checks passed.")
        return 0

    print("MTG CI boundary check failed:", file=sys.stderr)
    for violation in violations:
        print(f"- {violation}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
