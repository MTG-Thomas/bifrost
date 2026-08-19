"""Canonical authorization identities for immutable Workspace activation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from bifrost.workspace_release import canonical_digest

COMPUTED_EFFECTS_SCHEMA = "bifrost.workspace-computed-effects/v1"
ACTIVATION_CHALLENGE_SCHEMA = "bifrost.workspace-release-activation-challenge/v1"
AUTHORIZATION_EVIDENCE_SCHEMA = "bifrost.workspace-release-authorization/v1"
RISK_ACKNOWLEDGEMENT_SCHEMA = "bifrost.workspace-release-risk-acknowledgement/v1"
RISK_ACKNOWLEDGEMENT_DECISION = "activate_without_canary_or_effect_execution"


def _canonical_effects(effects: list[str]) -> list[str]:
    if not isinstance(effects, list):
        raise ValueError("computed effects must be a JSON list")
    values = list(effects)
    if (
        not values
        or any(not isinstance(effect, str) or not effect for effect in values)
        or values != sorted(set(values))
    ):
        raise ValueError("computed effects must be non-empty, sorted, and unique")
    return values


def _require_digest(value: str, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != 71
        or any(char not in "0123456789abcdef" for char in value[7:])
    ):
        raise ValueError(f"invalid {field}")
    return value


def _require_uuid(value: str, *, field: str) -> str:
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if str(parsed) != value:
        raise ValueError(f"invalid {field}")
    return value


def computed_effects_id(effects: list[str]) -> str:
    """Bind one canonical, explicitly classified effect set."""
    return canonical_digest(
        {
            "schema_version": COMPUTED_EFFECTS_SCHEMA,
            "effects": _canonical_effects(effects),
        }
    )


def activation_challenge(
    *,
    artifact_id: str,
    candidate_id: str,
    workspace_release_id: str,
    prepared_evidence_id: str,
    effective_manifest_id: str,
    governed_manifest_id: str,
    effective_registration_manifest_id: str,
    risk_class: str,
    computed_effects: list[str],
    policy_version: str,
    protected_source: Mapping[str, str],
) -> dict[str, Any]:
    """Build the exact server challenge an activation must authorize."""
    if risk_class not in {"R0", "R1", "R2"}:
        raise ValueError("invalid Workspace release risk class")
    if not isinstance(policy_version, str) or not policy_version:
        raise ValueError("invalid Workspace release policy version")
    artifact_id = _require_uuid(artifact_id, field="artifact ID")
    candidate_id = _require_digest(candidate_id, field="candidate ID")
    workspace_release_id = _require_digest(
        workspace_release_id, field="Workspace release ID"
    )
    prepared_evidence_id = _require_digest(
        prepared_evidence_id, field="prepared evidence ID"
    )
    effective_manifest_id = _require_digest(
        effective_manifest_id, field="effective manifest ID"
    )
    governed_manifest_id = _require_digest(
        governed_manifest_id, field="governed manifest ID"
    )
    effective_registration_manifest_id = _require_digest(
        effective_registration_manifest_id,
        field="effective registration manifest ID",
    )
    effects = _canonical_effects(computed_effects)
    if not isinstance(protected_source, Mapping):
        raise ValueError("invalid protected-main source identity")
    source = {
        "commit_sha": protected_source.get("commit_sha"),
        "tree_sha": protected_source.get("tree_sha"),
    }
    if (
        not isinstance(source["commit_sha"], str)
        or len(source["commit_sha"]) != 40
        or any(char not in "0123456789abcdef" for char in source["commit_sha"])
        or not isinstance(source["tree_sha"], str)
        or len(source["tree_sha"]) != 40
        or any(char not in "0123456789abcdef" for char in source["tree_sha"])
    ):
        raise ValueError("invalid protected-main source identity")
    payload = {
        "schema_version": ACTIVATION_CHALLENGE_SCHEMA,
        "artifact_id": artifact_id,
        "candidate_id": candidate_id,
        "workspace_release_id": workspace_release_id,
        "prepared_evidence_id": prepared_evidence_id,
        "effective_manifest_id": effective_manifest_id,
        "governed_manifest_id": governed_manifest_id,
        "effective_registration_manifest_id": (effective_registration_manifest_id),
        "risk_class": risk_class,
        "computed_effects": effects,
        "computed_effects_id": computed_effects_id(effects),
        "policy_version": policy_version,
        "protected_source": source,
        "required_authorization": (
            "reviewed_canary" if risk_class == "R0" else "risk_acknowledgement"
        ),
    }
    return {**payload, "challenge_id": canonical_digest(payload)}


def validate_activation_challenge(challenge: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild and exactly compare a serialized activation challenge."""
    expected = activation_challenge(
        artifact_id=str(challenge.get("artifact_id") or ""),
        candidate_id=str(challenge.get("candidate_id") or ""),
        workspace_release_id=str(challenge.get("workspace_release_id") or ""),
        prepared_evidence_id=str(challenge.get("prepared_evidence_id") or ""),
        effective_manifest_id=str(challenge.get("effective_manifest_id") or ""),
        governed_manifest_id=str(challenge.get("governed_manifest_id") or ""),
        effective_registration_manifest_id=str(
            challenge.get("effective_registration_manifest_id") or ""
        ),
        risk_class=str(challenge.get("risk_class") or ""),
        computed_effects=challenge.get("computed_effects") or [],
        policy_version=str(challenge.get("policy_version") or ""),
        protected_source=challenge.get("protected_source") or {},
    )
    if dict(challenge) != expected:
        raise ValueError("activation challenge is not canonical")
    return expected


def risk_acknowledgement(challenge: Mapping[str, Any]) -> dict[str, Any]:
    """Acknowledge a canonical R1/R2 challenge without claiming execution."""
    verified = validate_activation_challenge(challenge)
    if verified["risk_class"] not in {"R1", "R2"}:
        raise ValueError("R0 releases require a successful reviewed canary")
    payload = {
        "schema_version": RISK_ACKNOWLEDGEMENT_SCHEMA,
        "challenge_id": verified["challenge_id"],
        "candidate_id": verified["candidate_id"],
        "workspace_release_id": verified["workspace_release_id"],
        "prepared_evidence_id": verified["prepared_evidence_id"],
        "risk_class": verified["risk_class"],
        "computed_effects": verified["computed_effects"],
        "computed_effects_id": verified["computed_effects_id"],
        "protected_source": verified["protected_source"],
        "decision": RISK_ACKNOWLEDGEMENT_DECISION,
    }
    return {**payload, "acknowledgement_id": canonical_digest(payload)}


def validate_risk_acknowledgement(
    acknowledgement: Mapping[str, Any],
    challenge: Mapping[str, Any],
) -> dict[str, Any]:
    expected = risk_acknowledgement(challenge)
    if dict(acknowledgement) != expected:
        raise ValueError("risk acknowledgement does not match its exact challenge")
    return expected


__all__ = [
    "ACTIVATION_CHALLENGE_SCHEMA",
    "AUTHORIZATION_EVIDENCE_SCHEMA",
    "COMPUTED_EFFECTS_SCHEMA",
    "RISK_ACKNOWLEDGEMENT_DECISION",
    "RISK_ACKNOWLEDGEMENT_SCHEMA",
    "activation_challenge",
    "computed_effects_id",
    "risk_acknowledgement",
    "validate_activation_challenge",
    "validate_risk_acknowledgement",
]
