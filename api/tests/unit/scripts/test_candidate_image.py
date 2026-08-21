from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.ci import candidate_image


TREE_SHA = "a" * 40
SOURCE_SHA = "b" * 40
MAIN_SHA = "c" * 40
DIGEST = "sha256:" + "d" * 64
IMAGE = "ghcr.io/mtg-thomas/bifrost-api"
VERSION = "1.1.1-dev.456"


def _labels(*, tree_sha: str = TREE_SHA) -> dict[str, str]:
    return {
        "org.opencontainers.image.source": "https://github.com/MTG-Thomas/bifrost",
        "org.opencontainers.image.revision": SOURCE_SHA,
        "org.opencontainers.image.version": VERSION,
        "com.midtowntg.bifrost.source-tree": tree_sha,
        "com.midtowntg.bifrost.candidate": "true",
    }


class FakeCommands:
    def __init__(self, *, labels: dict[str, str] | None = None) -> None:
        self.labels = labels or _labels()
        self.commands: list[list[str]] = []

    def __call__(self, command, *, text, capture_output, check):
        assert text is True
        assert capture_output is True
        assert check is False
        command = list(command)
        self.commands.append(command)
        if command[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            template = command[-1]
            if template == "{{json .Manifest}}":
                return subprocess.CompletedProcess(
                    command, 0, json.dumps({"digest": DIGEST}), ""
                )
            if template == "{{json .Image.Config.Labels}}":
                return subprocess.CompletedProcess(
                    command, 0, json.dumps(self.labels), ""
                )
        return subprocess.CompletedProcess(command, 0, "verified\n", "")


def test_verify_candidate_binds_tree_source_version_signature(monkeypatch) -> None:
    fake = FakeCommands()
    monkeypatch.setattr(candidate_image.subprocess, "run", fake)

    candidate = candidate_image.verify_candidate(
        image=IMAGE,
        tree_sha=TREE_SHA,
        version=VERSION,
        source_sha=SOURCE_SHA,
        expected_digest=DIGEST,
    )

    assert candidate.digest == DIGEST
    assert candidate.ref == f"{IMAGE}:candidate-tree-{TREE_SHA}"
    verify_command = next(
        command for command in fake.commands if command[0] == "cosign"
    )
    assert verify_command[-1] == f"{IMAGE}@{DIGEST}"
    assert candidate_image.WORKFLOW_IDENTITY_RE in verify_command


def test_verify_candidate_rejects_a_tree_label_mismatch(monkeypatch) -> None:
    fake = FakeCommands(labels=_labels(tree_sha="e" * 40))
    monkeypatch.setattr(candidate_image.subprocess, "run", fake)

    with pytest.raises(candidate_image.CandidateImageError, match="source-tree"):
        candidate_image.verify_candidate(
            image=IMAGE,
            tree_sha=TREE_SHA,
            version=VERSION,
            source_sha=SOURCE_SHA,
            expected_digest=DIGEST,
        )

    assert not any(command[0] == "cosign" for command in fake.commands)


def test_verify_candidate_rejects_a_tag_that_moved_after_build(monkeypatch) -> None:
    fake = FakeCommands()
    monkeypatch.setattr(candidate_image.subprocess, "run", fake)

    with pytest.raises(candidate_image.CandidateImageError, match="newly built digest"):
        candidate_image.verify_candidate(
            image=IMAGE,
            tree_sha=TREE_SHA,
            version=VERSION,
            source_sha=SOURCE_SHA,
            expected_digest="sha256:" + "e" * 64,
        )

    assert not any(command[0] == "cosign" for command in fake.commands)


def test_promote_retags_exact_digest_and_writes_main_attestation(
    monkeypatch, tmp_path: Path
) -> None:
    fake = FakeCommands()
    monkeypatch.setattr(candidate_image.subprocess, "run", fake)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    output = tmp_path / "github-output"

    candidate_image.promote_candidate(
        component="api",
        image=IMAGE,
        tree_sha=TREE_SHA,
        version=VERSION,
        main_source_sha=MAIN_SHA,
        tags=(VERSION, "dev", f"sha-{MAIN_SHA[:7]}"),
        github_output=output,
    )

    create = next(
        command
        for command in fake.commands
        if command[:4] == ["docker", "buildx", "imagetools", "create"]
    )
    assert create[-1] == f"{IMAGE}@{DIGEST}"
    assert create.count("--tag") == 3
    assert f"{IMAGE}:dev" in create

    predicate_path = tmp_path / "bifrost-api-image-promotion.json"
    predicate = json.loads(predicate_path.read_text(encoding="utf-8"))
    assert predicate == {
        "candidate_digest": DIGEST,
        "candidate_ref": f"{IMAGE}:candidate-tree-{TREE_SHA}",
        "candidate_source_sha": SOURCE_SHA,
        "component": "api",
        "promoted_tags": [VERSION, "dev", f"sha-{MAIN_SHA[:7]}"],
        "protected_main_source_sha": MAIN_SHA,
        "repository": "MTG-Thomas/bifrost",
        "schema_version": "bifrost.image-promotion/v1",
        "source_tree_sha": TREE_SHA,
        "version": VERSION,
    }
    assert f"digest={DIGEST}" in output.read_text(encoding="utf-8")


def test_promote_fails_when_a_stable_tag_does_not_keep_the_candidate_digest(
    monkeypatch,
) -> None:
    fake = FakeCommands()
    inspect_count = 0

    def mismatched_tag(command, *, text, capture_output, check):
        nonlocal inspect_count
        result = fake(command, text=text, capture_output=capture_output, check=check)
        if command[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            inspect_count += 1
            if inspect_count == 3:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({"digest": "sha256:" + "f" * 64}),
                    "",
                )
        return result

    monkeypatch.setattr(candidate_image.subprocess, "run", mismatched_tag)

    with pytest.raises(
        candidate_image.CandidateImageError, match="tested candidate digest"
    ):
        candidate_image.promote_candidate(
            component="api",
            image=IMAGE,
            tree_sha=TREE_SHA,
            version=VERSION,
            main_source_sha=MAIN_SHA,
            tags=("dev",),
        )
