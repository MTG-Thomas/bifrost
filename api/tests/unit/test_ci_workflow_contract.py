"""Static contracts for MTG's serialized delivery lane and nightly coverage."""

from pathlib import Path
from typing import Any

import yaml


def _repository_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / ".github" / "workflows" / "ci.yml").is_file():
            return candidate
    raise RuntimeError("could not locate repository workflow sources")


REPO_ROOT = _repository_root()
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CODEQL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "codeql.yml"
NIGHTLY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nightly.yml"


def _load_workflow(path: Path) -> dict[str, Any]:
    loaded = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def test_ci_supports_native_merge_groups_during_queue_cutover() -> None:
    workflow = _load_workflow(CI_WORKFLOW)
    triggers = workflow["on"]
    jobs = workflow["jobs"]

    assert {"merge_group", "pull_request", "push", "workflow_dispatch"} <= triggers.keys()
    assert triggers["merge_group"]["types"] == ["checks_requested"]
    assert "build-dev-candidate" not in jobs
    assert "test-client-smoke" not in jobs
    assert "verify_merge_candidate.py" not in CI_WORKFLOW.read_text()

    codeql_triggers = _load_workflow(CODEQL_WORKFLOW)["on"]
    assert codeql_triggers["merge_group"]["types"] == ["checks_requested"]


def test_mtg_candidate_images_are_promoted_without_rebuild() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    candidates = jobs["build-candidate-images"]
    promotion = jobs["build-dev"]

    assert "github.event_name == 'pull_request'" in candidates["if"]
    assert "github.event_name == 'merge_group'" in candidates["if"]
    assert "MTG-Thomas/bifrost" in candidates["if"]
    assert "Midtown-Technology-Group/bifrost" in candidates["if"]
    assert promotion["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"

    checkout = next(
        step for step in candidates["steps"]
        if step["name"] == "Checkout exact pull-request head"
    )
    identity = next(
        step for step in candidates["steps"]
        if step["name"] == "Compute immutable candidate identity"
    )
    build = next(
        step for step in candidates["steps"]
        if step["name"] == "Build immutable candidate"
    )
    verify = next(
        step for step in candidates["steps"]
        if step["name"] == "Verify candidate identity and signature"
    )
    assert "github.event.merge_group.base_sha" in identity["env"]["BASE_SHA"]
    for value in (
        checkout["with"]["ref"],
        identity["env"]["HEAD_SHA"],
        verify["env"]["HEAD_SHA"],
    ):
        assert "github.sha" in value
    assert "github.sha" in build["with"]["labels"]

    source = "\n".join(step.get("run", "") for step in promotion["steps"])
    assert "candidate_image.py promote" in source
    assert "--tree-sha" in source
    assert "--main-source-sha" in source


def test_release_tags_pass_skipped_candidate_jobs_through_manifest_gate() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    verification = jobs["verify-release-manifest"]
    prerequisites = {"test-unit", "test-e2e-gate", "lint", "mcp-conformance"}

    assert set(verification["needs"]) == prerequisites
    verification_condition = " ".join(verification["if"].split())
    assert "always()" in verification_condition
    assert "startsWith(github.ref, 'refs/tags/v')" in verification_condition
    for prerequisite in prerequisites:
        assert f"needs.{prerequisite}.result == 'success'" in verification_condition

    for job_name in ("build-api", "build-client", "build-worker"):
        job = jobs[job_name]
        assert job["needs"] == ["verify-release-manifest"]
        condition = " ".join(job["if"].split())
        assert "always()" in condition
        assert "needs.verify-release-manifest.result == 'success'" in condition

    release = jobs["create-release"]
    assert set(release["needs"]) == {"build-api", "build-client", "build-worker"}
    release_condition = " ".join(release["if"].split())
    assert "always()" in release_condition
    for prerequisite in release["needs"]:
        assert f"needs.{prerequisite}.result == 'success'" in release_condition


def test_action_pin_versions_are_verified_in_ci() -> None:
    lint = _load_workflow(CI_WORKFLOW)["jobs"]["lint"]
    step = next(
        step for step in lint["steps"] if step.get("name") == "Check GitHub Action pins"
    )
    assert step["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert "--verify-versions" in step["run"]


def test_nightly_owns_full_browser_slow_coverage_and_clean_builds() -> None:
    workflow = _load_workflow(NIGHTLY_WORKFLOW)
    assert {"schedule", "workflow_dispatch"} <= workflow["on"].keys()
    assert set(workflow["jobs"]) == {
        "product-browser",
        "slow-unit-contracts",
        "backend-coverage",
        "clean-production-build",
    }

    expected_commands = {
        "product-browser": "./test.sh client nightly",
        "slow-unit-contracts": "-m slow",
        "backend-coverage": "--cov-report=xml:/tmp/bifrost/coverage.xml",
    }
    for job_name, expected in expected_commands.items():
        job = workflow["jobs"][job_name]
        run_step = next(
            step for step in job["steps"] if "./test.sh stack up" in step.get("run", "")
        )
        assert run_step["env"]["BIFROST_TEST_USE_CLEAN_BOOT"] == "1"
        assert expected in run_step["run"]

    clean_builds = [
        step
        for step in workflow["jobs"]["clean-production-build"]["steps"]
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]
    assert len(clean_builds) == 2
    assert all(step["with"]["no-cache"] == "true" for step in clean_builds)
