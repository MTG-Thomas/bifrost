from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
@pytest.mark.skipif(os.name == "nt", reason="script is exercised in Linux CI")
@pytest.mark.parametrize(
    (
        "dependency_path",
        "initial",
        "updated",
        "expected_api",
        "expected_client",
        "expected_playwright",
    ),
    [
        ("requirements.lock", "pytest==1\n", "pytest==2\n", True, False, False),
        (
            "api/src/services/sdk_package/package-lock.json",
            '{"lockfileVersion": 3, "packages": {}}\n',
            '{"lockfileVersion": 3, "packages": {"node_modules/esbuild": {}}}\n',
            True,
            False,
            False,
        ),
        (
            "client/Dockerfile.playwright",
            "FROM example.invalid/playwright:1\n",
            "FROM example.invalid/playwright:2\n",
            False,
            False,
            True,
        ),
        (
            "client/package-lock.json",
            '{"lockfileVersion": 3, "packages": {}}\n',
            '{"lockfileVersion": 3, "packages": {"node_modules/vite": {}}}\n',
            False,
            True,
            True,
        ),
    ],
)
def test_pull_request_head_ref_detects_dependency_changes(
    tmp_path: Path,
    dependency_path: str,
    initial: str,
    updated: str,
    expected_api: bool,
    expected_client: bool,
    expected_playwright: bool,
):
    repo = tmp_path / "repo"
    origin = tmp_path / "origin.git"
    repo.mkdir()
    api_root = Path(__file__).resolve().parents[3]
    source_script_candidates = [
        api_root.parent / "scripts" / "ci" / "detect-test-image-inputs.sh",
        Path("/repo/scripts/ci/detect-test-image-inputs.sh"),
    ]
    source_script = next(path for path in source_script_candidates if path.exists())
    script = repo / "scripts" / "ci" / "detect-test-image-inputs.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(source_script, script)

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "CI Test"], cwd=repo, check=True)
    dependency_file = repo / dependency_path
    dependency_file.parent.mkdir(parents=True, exist_ok=True)
    dependency_file.write_text(initial)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True)
    subprocess.run(["git", "clone", "--bare", str(repo), str(origin)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=repo, check=True)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=True)
    dependency_file.write_text(updated)
    subprocess.run(["git", "commit", "-am", "change deps"], cwd=repo, check=True)
    subprocess.run(["git", "push", "origin", "feature"], cwd=repo, check=True)

    env = {
        **os.environ,
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_BASE_REF": "main",
        "GITHUB_HEAD_REF": "feature",
        "GITHUB_OUTPUT": "github-output.txt",
        "GITHUB_STEP_SUMMARY": "github-summary.md",
    }
    subprocess.run(["bash", "scripts/ci/detect-test-image-inputs.sh"], cwd=repo, env=env, check=True)

    output_path = repo / "github-output.txt"
    output = output_path.read_text()
    assert f"api_changed={str(expected_api).lower()}" in output
    assert f"client_changed={str(expected_client).lower()}" in output
    assert f"playwright_changed={str(expected_playwright).lower()}" in output
