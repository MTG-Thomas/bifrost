"""Release jobs must survive intentional skipped CI ancestors and fail closed."""

import json
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


def test_mtg_pr_images_build_in_parallel_and_main_promotes_exact_digests() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    candidate_build = jobs["build-candidate-images"]
    candidate_gate = jobs["candidate-images"]
    main_publish = jobs["build-dev"]

    assert "needs" not in candidate_build
    assert candidate_gate["name"] == "Candidate Images"
    assert candidate_gate["needs"] == "build-candidate-images"
    assert set(candidate_build["strategy"]["matrix"]["include"][0]) >= {
        "component",
        "image",
        "dockerfile",
        "version_arg",
    }

    build_step = next(
        step
        for step in candidate_build["steps"]
        if step["name"] == "Build immutable candidate"
    )
    assert (
        "candidate-tree-${{ steps.identity.outputs.tree_sha }}"
        in build_step["with"]["tags"]
    )
    assert "com.midtowntg.bifrost.source-tree" in build_step["with"]["labels"]
    assert "com.midtowntg.bifrost.candidate=true" in build_step["with"]["labels"]

    promotion_steps = [
        step
        for step in main_publish["steps"]
        if step["name"].startswith("Promote tested")
    ]
    assert [step["name"] for step in promotion_steps] == [
        "Promote tested API candidate",
        "Promote tested worker candidate",
        "Promote tested client candidate",
    ]
    for step in promotion_steps:
        assert "api/scripts/ci/candidate_image.py promote" in step["run"]
        assert "--tree-sha" in step["run"]
        assert "--main-source-sha" in step["run"]

    policy = json.loads((REPO_ROOT / ".github/serialized-merge-queue.json").read_text())
    assert {"context": "Candidate Images", "app_id": 15368} in policy["required_checks"]
