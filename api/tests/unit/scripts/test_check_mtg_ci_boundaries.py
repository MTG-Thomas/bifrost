from pathlib import Path

import pytest

from scripts.check_mtg_ci_boundaries import (
    REQUIRED_CI_JOB_NAMES,
    _repo_root,
    _resolve_workflow_path,
    check_ci_workflow,
    check_serialized_queue_workflow,
)


def test_resolve_workflow_path_rejects_paths_outside_repo():
    outside = _repo_root().parent / "outside.yml"
    with pytest.raises(ValueError, match="must stay within"):
        _resolve_workflow_path(outside)


def test_repo_root_never_resolves_to_filesystem_root():
    root = _repo_root()
    assert root != root.parent


def test_required_ci_check_identities_are_preserved():
    workflow = _repo_root() / ".github/workflows/ci.yml"

    assert check_ci_workflow(workflow) == []
    assert check_serialized_queue_workflow(
        _repo_root() / ".github/workflows/serialized-merge-queue.yml"
    ) == []


def test_required_ci_check_identity_change_is_rejected(tmp_path: Path):
    workflow = _repo_root() / ".github/workflows/ci.yml"
    mutated = tmp_path / "ci.yml"
    mutated.write_text(
        workflow.read_text(encoding="utf-8").replace(
            f"name: {REQUIRED_CI_JOB_NAMES['test-unit']}",
            "name: Renamed Unit Gate",
            1,
        ),
        encoding="utf-8",
    )

    violations = check_ci_workflow(mutated)

    assert any("required CI check identity changed" in item for item in violations)


def test_merge_group_trigger_is_required(tmp_path: Path):
    workflow = _repo_root() / ".github/workflows/ci.yml"
    mutated = tmp_path / "ci.yml"
    mutated.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "  merge_group:\n    types: [checks_requested]\n", "", 1
        ),
        encoding="utf-8",
    )

    assert any("merge_group trigger" in item for item in check_ci_workflow(mutated))


def test_merge_group_trigger_rejects_other_activity_types(tmp_path: Path):
    workflow = _repo_root() / ".github/workflows/ci.yml"
    mutated = tmp_path / "ci.yml"
    mutated.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "types: [checks_requested]", "types: [destroyed]", 1
        ),
        encoding="utf-8",
    )

    violations = check_ci_workflow(mutated)
    assert any("only checks_requested" in item for item in violations)


def test_required_test_jobs_reject_preloaded_third_party_actions(tmp_path: Path):
    workflow = _repo_root() / ".github/workflows/ci.yml"
    original = workflow.read_text(encoding="utf-8")

    for replacement in (
        "uses: docker/setup-buildx-action@" + "a" * 40,
        "uses: codecov/codecov-action@" + "b" * 40,
    ):
        mutated = tmp_path / f"ci-{replacement.split('/')[0]}.yml"
        mutated.write_text(
            original.replace(
                "run: bash api/scripts/ci/prepare-test-images.sh api client",
                replacement,
                1,
            ),
            encoding="utf-8",
        )

        violations = check_ci_workflow(mutated)

        assert any("must not preload third-party action" in item for item in violations)


def test_serialized_queue_contract_is_preserved(tmp_path: Path):
    workflow = _repo_root() / ".github/workflows/serialized-merge-queue.yml"
    contents = workflow.read_text(encoding="utf-8")
    mutated = tmp_path / "queue.yml"
    mutated.write_text(contents.replace("cancel-in-progress: false", "cancel-in-progress: true"), encoding="utf-8")
    assert any("cancel-in-progress: false" in item for item in check_serialized_queue_workflow(mutated))
