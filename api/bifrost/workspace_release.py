"""Canonical content identities shared by Workspace release clients and runtime."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

WORKSPACE_FILE_MANIFEST_SCHEMA = "bifrost.workspace-file-manifest/v1"
WORKSPACE_FORWARD_CLOSURE_SCHEMA = "bifrost.workspace-forward-closure/v1"
WORKSPACE_COHORT_CLOSURE_SCHEMA = "bifrost.workspace-cohort-closure/v1"
WORKSPACE_RELEASE_CONTENT_SCHEMA = "bifrost.workspace-release-content/v1"
WORKSPACE_RELEASE_SCHEMA = "bifrost.workspace-release/v1"
WORKSPACE_REGISTRATION_MANIFEST_SCHEMA = "bifrost.workspace-registration-manifest/v1"


def _sha256_value(value: str) -> str:
    """Canonicalize a file digest accepted with or without its algorithm prefix."""

    normalized = value.removeprefix("sha256:").lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"invalid SHA-256 digest: {value!r}")
    return normalized


def _file_hashes(files: Mapping[str, str]) -> dict[str, str]:
    return {path: _sha256_value(value) for path, value in sorted(files.items())}


def canonical_digest(payload: Any) -> str:
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def workspace_manifest_id(files: Mapping[str, str]) -> str:
    return canonical_digest(
        {
            "schema": WORKSPACE_FILE_MANIFEST_SCHEMA,
            "files": _file_hashes(files),
        }
    )


def workspace_registration_manifest_id(
    registrations: Mapping[str, Mapping[str, Any]],
) -> str:
    return canonical_digest(
        {
            "schema": WORKSPACE_REGISTRATION_MANIFEST_SCHEMA,
            "registrations": {
                key: dict(value) for key, value in sorted(registrations.items())
            },
        }
    )


def repo_v1_release_id(files: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for path, value in _file_hashes(files).items():
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return f"repo-v1:{digest.hexdigest()}"


def workspace_closure_id(entry: Mapping[str, str], files: Mapping[str, str]) -> str:
    return canonical_digest(
        {
            "schema": WORKSPACE_FORWARD_CLOSURE_SCHEMA,
            "entry": dict(entry),
            "files": _file_hashes(files),
        }
    )


def workspace_cohort_closure_id(
    entry: Mapping[str, str],
    files: Mapping[str, str],
    cohort_paths: list[str],
) -> str:
    return canonical_digest(
        {
            "schema": WORKSPACE_COHORT_CLOSURE_SCHEMA,
            "entry": dict(entry),
            "cohort_paths": sorted(cohort_paths),
            "files": _file_hashes(files),
        }
    )


def workspace_content_id(entry: Mapping[str, str], closure_id: str) -> str:
    return canonical_digest(
        {
            "schema": WORKSPACE_RELEASE_CONTENT_SCHEMA,
            "entry": dict(entry),
            "closure_id": closure_id,
        }
    )


def workspace_release_id(payload: Mapping[str, Any]) -> str:
    return canonical_digest({"schema": WORKSPACE_RELEASE_SCHEMA, **dict(payload)})


__all__ = [
    "WORKSPACE_FILE_MANIFEST_SCHEMA",
    "WORKSPACE_FORWARD_CLOSURE_SCHEMA",
    "WORKSPACE_COHORT_CLOSURE_SCHEMA",
    "WORKSPACE_RELEASE_CONTENT_SCHEMA",
    "WORKSPACE_RELEASE_SCHEMA",
    "WORKSPACE_REGISTRATION_MANIFEST_SCHEMA",
    "canonical_digest",
    "repo_v1_release_id",
    "workspace_closure_id",
    "workspace_cohort_closure_id",
    "workspace_content_id",
    "workspace_manifest_id",
    "workspace_registration_manifest_id",
    "workspace_release_id",
]
