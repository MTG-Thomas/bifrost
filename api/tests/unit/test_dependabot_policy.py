"""Contracts for the repository's conservative dependency update policy."""

from pathlib import Path
from typing import Any

import yaml


def _repository_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / ".github" / "dependabot.yml").is_file():
            return candidate
    raise RuntimeError("could not locate repository Dependabot configuration")


REPO_ROOT = _repository_root()
DEPENDABOT_CONFIG = REPO_ROOT / ".github" / "dependabot.yml"
AUTO_MERGE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dependabot-auto-merge.yml"


def _load(path: Path) -> dict[str, Any]:
    loaded = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def test_routine_updates_are_limited_to_cooled_down_patch_and_minor_bumps() -> None:
    config = _load(DEPENDABOT_CONFIG)
    updates = config["updates"]
    maintained = [
        update
        for update in updates
        if update["package-ecosystem"] in {"pip", "npm", "github-actions"}
    ]

    assert len(maintained) == 3
    for update in maintained:
        assert update["schedule"]["interval"] == "weekly"
        assert update["open-pull-requests-limit"] in {"5", "10"}
        assert update["allow"] == [
            {
                next(
                    key
                    for key in ("dependency-type", "dependency-name")
                    if key in update["allow"][0]
                ): update["allow"][0].get("dependency-type", "*"),
                "update-types": [
                    "version-update:semver-patch",
                    "version-update:semver-minor",
                ],
            }
        ]
        assert update["cooldown"] == {
            "default-days": "7",
            "semver-patch-days": "7",
            "semver-minor-days": "14",
            "semver-major-days": "90",
        }


def test_docker_only_opens_security_update_pull_requests() -> None:
    updates = _load(DEPENDABOT_CONFIG)["updates"]
    docker_updates = [
        update for update in updates if update["package-ecosystem"] == "docker"
    ]

    assert len(docker_updates) == 2
    assert all(update["open-pull-requests-limit"] == "0" for update in docker_updates)


def test_fastmcp_prereleases_are_frozen_until_the_stable_release() -> None:
    updates = _load(DEPENDABOT_CONFIG)["updates"]
    pip = next(update for update in updates if update["package-ecosystem"] == "pip")

    assert {"dependency-name": "fastmcp", "versions": [">4.0.0b1,<4.0.0"]} in pip[
        "ignore"
    ]


def test_security_updates_are_not_labeled_for_manual_review() -> None:
    jobs = _load(AUTO_MERGE_WORKFLOW)["jobs"]
    steps = jobs["auto-merge"]["steps"]
    label_step = next(step for step in steps if step["name"].startswith("Label unexpected"))

    assert "steps.metadata.outputs.alert-state == ''" in label_step["if"]
