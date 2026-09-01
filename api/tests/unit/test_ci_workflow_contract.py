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
QUEUE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "serialized-merge-queue.yml"
NIGHTLY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nightly.yml"


def _load_workflow(path: Path) -> dict[str, Any]:
    loaded = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def test_ci_retains_mtg_serialized_queue_boundary() -> None:
    workflow = _load_workflow(CI_WORKFLOW)
    triggers = workflow["on"]
    jobs = workflow["jobs"]

    assert {"pull_request", "push", "workflow_dispatch"} <= triggers.keys()
    assert "merge_group" not in triggers
    assert "build-dev-candidate" not in jobs
    assert "test-client-smoke" not in jobs
    assert "verify_merge_candidate.py" not in CI_WORKFLOW.read_text()

    queue = _load_workflow(QUEUE_WORKFLOW)
    assert queue["concurrency"]["group"] == "serialized-merge-queue-main"
    assert queue["concurrency"]["cancel-in-progress"] == "false"
    assert "advance" in queue["jobs"]


def test_mtg_candidate_images_are_promoted_without_rebuild() -> None:
    jobs = _load_workflow(CI_WORKFLOW)["jobs"]
    candidates = jobs["build-candidate-images"]
    promotion = jobs["build-dev"]

    assert "github.event_name == 'pull_request'" in candidates["if"]
    assert "MTG-Thomas/bifrost" in candidates["if"]
    assert "inputs.queue_post_merge" in promotion["if"]

    source = "\n".join(step.get("run", "") for step in promotion["steps"])
    assert "candidate_image.py promote" in source
    assert "--tree-sha" in source
    assert "--main-source-sha" in source


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
