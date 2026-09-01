from __future__ import annotations

import os
from pathlib import Path
import subprocess


API_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = API_ROOT / "scripts/ci/prepare-test-images.sh"


def _run(tmp_path: Path, *, api_changed: str, pull_fails: bool = False) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$DOCKER_LOG"
if [ "${1:-}" = pull ] && [ "${PULL_FAILS:-false}" = true ]; then
  exit 1
fi
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    summary = tmp_path / "summary.md"
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "PULL_FAILS": str(pull_fails).lower(),
        "REGISTRY": "ghcr.io",
        "GHCR_USERNAME": "ci-user",
        "GHCR_TOKEN": "test-token",
        "CI_TEST_IMAGE_TAG": "main",
        "CI_API_TEST_IMAGE": "example/api",
        "API_IMAGE_CHANGED": api_changed,
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    subprocess.run(["bash", str(SCRIPT), "api"], cwd=API_ROOT, env=env, check=True)
    return log.read_text(encoding="utf-8").splitlines()


def test_unchanged_image_is_pulled_and_tagged_without_build(tmp_path: Path) -> None:
    calls = _run(tmp_path, api_changed="false")

    assert calls == [
        "login ghcr.io --username ci-user --password-stdin",
        "pull ghcr.io/example/api:main",
        "tag ghcr.io/example/api:main bifrost-test-api-dev:latest",
        "logout ghcr.io",
    ]


def test_changed_image_builds_from_reviewed_source_with_pulled_cache(tmp_path: Path) -> None:
    calls = _run(tmp_path, api_changed="true")

    assert calls == [
        "login ghcr.io --username ci-user --password-stdin",
        "pull ghcr.io/example/api:main",
        (
            "build --file ./api/Dockerfile.dev --tag bifrost-test-api-dev:latest "
            "--cache-from ghcr.io/example/api:main ."
        ),
        "logout ghcr.io",
    ]


def test_pull_failure_falls_back_to_fail_closed_local_build(tmp_path: Path) -> None:
    calls = _run(tmp_path, api_changed="false", pull_fails=True)

    assert calls == [
        "login ghcr.io --username ci-user --password-stdin",
        "pull ghcr.io/example/api:main",
        "build --file ./api/Dockerfile.dev --tag bifrost-test-api-dev:latest .",
        "logout ghcr.io",
    ]


def test_client_e2e_builds_reviewed_production_image(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$DOCKER_LOG"
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "REGISTRY": "ghcr.io",
        "GHCR_USERNAME": "ci-user",
        "GHCR_TOKEN": "test-token",
        "CI_TEST_IMAGE_TAG": "main",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    }
    subprocess.run(
        ["bash", str(SCRIPT), "client-e2e"],
        cwd=API_ROOT,
        env=env,
        check=True,
    )

    assert log.read_text(encoding="utf-8").splitlines() == [
        "login ghcr.io --username ci-user --password-stdin",
        (
            "build --file ./client/Dockerfile --tag "
            "bifrost-test-client-e2e:latest --target production "
            "--build-arg VITE_BIFROST_VERSION=test ./client"
        ),
        "logout ghcr.io",
    ]


def test_playwright_image_uses_the_compose_service_tag() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert '"bifrost-test-playwright-runner:latest"' in script
