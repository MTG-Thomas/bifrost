#!/usr/bin/env python3
"""Verify and promote an immutable pull request container image."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+-dev\.[0-9]+$")
IMAGE_RE = re.compile(r"^ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+$")
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
WORKFLOW_IDENTITY_RE = (
    r"^https://github\.com/MTG-Thomas/bifrost/\.github/workflows/ci\.yml@refs/.*$"
)
OIDC_ISSUER = "https://token.actions.githubusercontent.com"


class CandidateImageError(RuntimeError):
    """Raised when candidate evidence is absent or does not match reviewed source."""


@dataclass(frozen=True)
class CandidateImage:
    image: str
    ref: str
    digest: str
    tree_sha: str
    source_sha: str
    version: str


def _run(command: Sequence[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no command output"
        raise CandidateImageError(f"{' '.join(command[:3])} failed: {detail}")
    return result.stdout.strip()


def _inspect_json(ref: str, template: str) -> Any:
    raw = _run(["docker", "buildx", "imagetools", "inspect", ref, "--format", template])
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CandidateImageError(f"{ref} returned malformed image metadata") from exc


def _require_sha1(value: str, label: str) -> str:
    value = value.lower()
    if not SHA1_RE.fullmatch(value):
        raise CandidateImageError(f"{label} must be a full lowercase Git SHA")
    return value


def _require_image(value: str) -> str:
    value = value.lower()
    if not IMAGE_RE.fullmatch(value):
        raise CandidateImageError("image must be an untagged ghcr.io repository")
    return value


def _require_version(value: str) -> str:
    if not VERSION_RE.fullmatch(value):
        raise CandidateImageError(
            "version must be an exact Bifrost development version"
        )
    return value


def _candidate_ref(image: str, tree_sha: str) -> str:
    return f"{image}:candidate-tree-{tree_sha}"


def _image_digest(ref: str) -> str:
    manifest = _inspect_json(ref, "{{json .Manifest}}")
    digest = manifest.get("digest") if isinstance(manifest, dict) else None
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise CandidateImageError(
            f"{ref} did not resolve to one immutable SHA-256 digest"
        )
    return digest


def verify_candidate(
    *,
    image: str,
    tree_sha: str,
    version: str,
    source_sha: str | None = None,
    expected_digest: str | None = None,
) -> CandidateImage:
    image = _require_image(image)
    tree_sha = _require_sha1(tree_sha, "tree SHA")
    version = _require_version(version)
    if source_sha is not None:
        source_sha = _require_sha1(source_sha, "source SHA")
    if expected_digest is not None and not SHA256_RE.fullmatch(expected_digest):
        raise CandidateImageError("expected digest must be a full SHA-256 digest")

    ref = _candidate_ref(image, tree_sha)
    digest = _image_digest(ref)
    if expected_digest is not None and digest != expected_digest:
        raise CandidateImageError(f"{ref} does not resolve to the newly built digest")
    labels = _inspect_json(ref, "{{json .Image.Config.Labels}}")
    if not isinstance(labels, dict):
        raise CandidateImageError(f"{ref} has no inspectable image labels")

    expected = {
        "org.opencontainers.image.source": "https://github.com/MTG-Thomas/bifrost",
        "org.opencontainers.image.version": version,
        "com.midtowntg.bifrost.source-tree": tree_sha,
        "com.midtowntg.bifrost.candidate": "true",
    }
    for name, expected_value in expected.items():
        if labels.get(name) != expected_value:
            raise CandidateImageError(
                f"{ref} label {name!r} does not match the reviewed candidate"
            )

    candidate_source_sha = str(
        labels.get("org.opencontainers.image.revision", "")
    ).lower()
    _require_sha1(candidate_source_sha, "candidate source SHA")
    if source_sha is not None and candidate_source_sha != source_sha:
        raise CandidateImageError(
            f"{ref} was not built from the expected pull-request head"
        )

    _run(
        [
            "cosign",
            "verify",
            "--certificate-identity-regexp",
            WORKFLOW_IDENTITY_RE,
            "--certificate-oidc-issuer",
            OIDC_ISSUER,
            f"{image}@{digest}",
        ]
    )
    return CandidateImage(
        image=image,
        ref=ref,
        digest=digest,
        tree_sha=tree_sha,
        source_sha=candidate_source_sha,
        version=version,
    )


def _write_output(path: Path | None, values: dict[str, str]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def _predicate_path(component: str) -> Path:
    runner_temp = Path(os.environ.get("RUNNER_TEMP", "/tmp"))
    return runner_temp / f"bifrost-{component}-image-promotion.json"


def promote_candidate(
    *,
    component: str,
    image: str,
    tree_sha: str,
    version: str,
    main_source_sha: str,
    tags: Sequence[str],
    github_output: Path | None = None,
) -> CandidateImage:
    main_source_sha = _require_sha1(main_source_sha, "protected-main source SHA")
    if not component or not re.fullmatch(r"[a-z][a-z0-9-]*", component):
        raise CandidateImageError("component must be a lowercase image name")
    if not tags or any(not TAG_RE.fullmatch(tag) for tag in tags):
        raise CandidateImageError("every promoted image tag must be valid")

    candidate = verify_candidate(image=image, tree_sha=tree_sha, version=version)
    command = ["docker", "buildx", "imagetools", "create"]
    for tag in tags:
        command.extend(["--tag", f"{candidate.image}:{tag}"])
    command.append(f"{candidate.image}@{candidate.digest}")
    _run(command)

    for tag in tags:
        promoted_ref = f"{candidate.image}:{tag}"
        if _image_digest(promoted_ref) != candidate.digest:
            raise CandidateImageError(
                f"{promoted_ref} did not resolve to the tested candidate digest"
            )

    predicate_path = _predicate_path(component)
    predicate = {
        "schema_version": "bifrost.image-promotion/v1",
        "component": component,
        "repository": "MTG-Thomas/bifrost",
        "protected_main_source_sha": main_source_sha,
        "source_tree_sha": candidate.tree_sha,
        "candidate_source_sha": candidate.source_sha,
        "candidate_ref": candidate.ref,
        "candidate_digest": candidate.digest,
        "version": candidate.version,
        "promoted_tags": list(tags),
    }
    predicate_path.write_text(
        json.dumps(predicate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_output(
        github_output,
        {
            "digest": candidate.digest,
            "candidate_ref": candidate.ref,
            "candidate_source_sha": candidate.source_sha,
            "predicate_path": str(predicate_path),
        },
    )
    return candidate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify", help="verify one PR candidate image")
    verify.add_argument("--image", required=True)
    verify.add_argument("--tree-sha", required=True)
    verify.add_argument("--version", required=True)
    verify.add_argument("--source-sha", required=True)
    verify.add_argument("--expected-digest", required=True)
    verify.add_argument("--github-output", type=Path)

    promote = subparsers.add_parser(
        "promote", help="retag one verified candidate without rebuilding it"
    )
    promote.add_argument("--component", required=True)
    promote.add_argument("--image", required=True)
    promote.add_argument("--tree-sha", required=True)
    promote.add_argument("--version", required=True)
    promote.add_argument("--main-source-sha", required=True)
    promote.add_argument("--tag", action="append", required=True, dest="tags")
    promote.add_argument("--github-output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "verify":
            candidate = verify_candidate(
                image=args.image,
                tree_sha=args.tree_sha,
                version=args.version,
                source_sha=args.source_sha,
                expected_digest=args.expected_digest,
            )
            _write_output(
                args.github_output,
                {
                    "digest": candidate.digest,
                    "candidate_ref": candidate.ref,
                    "candidate_source_sha": candidate.source_sha,
                },
            )
        else:
            promote_candidate(
                component=args.component,
                image=args.image,
                tree_sha=args.tree_sha,
                version=args.version,
                main_source_sha=args.main_source_sha,
                tags=args.tags,
                github_output=args.github_output,
            )
    except CandidateImageError as exc:
        _parser().error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
