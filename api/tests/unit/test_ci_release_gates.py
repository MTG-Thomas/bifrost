"""Release jobs must survive intentional skipped CI ancestors and fail closed."""

from pathlib import Path

import yaml


REPO_ROOT = Path.cwd()


def test_tag_release_jobs_explicitly_gate_required_results() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]

    verify_condition = jobs["verify-release-manifest"]["if"]
    assert "always()" in verify_condition
    assert "startsWith(github.ref, 'refs/tags/v')" in verify_condition
    for required in ("test-unit", "test-e2e-gate", "lint", "mcp-conformance"):
        assert f"needs.{required}.result == 'success'" in verify_condition

    for build_job in ("build-api", "build-client", "build-worker"):
        condition = jobs[build_job]["if"]
        assert "always()" in condition
        assert "startsWith(github.ref, 'refs/tags/v')" in condition
        assert "needs.verify-release-manifest.result == 'success'" in condition

    release_condition = jobs["create-release"]["if"]
    assert "always()" in release_condition
    assert "startsWith(github.ref, 'refs/tags/v')" in release_condition
    for build_job in ("build-api", "build-client", "build-worker"):
        assert f"needs.{build_job}.result == 'success'" in release_condition
