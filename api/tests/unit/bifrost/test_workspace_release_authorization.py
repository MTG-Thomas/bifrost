from __future__ import annotations

import pytest

from bifrost.workspace_release_authorization import (
    AUTHORIZATION_EVIDENCE_SCHEMA,
    RISK_ACKNOWLEDGEMENT_DECISION,
    activation_challenge,
    computed_effects_id,
    risk_acknowledgement,
    validate_activation_challenge,
    validate_risk_acknowledgement,
)


def _challenge(*, risk_class: str = "R2", effects: list[str] | None = None):
    return activation_challenge(
        artifact_id="11111111-1111-1111-1111-111111111111",
        candidate_id="sha256:" + "1" * 64,
        workspace_release_id="sha256:" + "2" * 64,
        prepared_evidence_id="sha256:" + "3" * 64,
        effective_manifest_id="sha256:" + "4" * 64,
        governed_manifest_id="sha256:" + "5" * 64,
        effective_registration_manifest_id="sha256:" + "6" * 64,
        risk_class=risk_class,
        computed_effects=effects or ["integration.write:halopsa"],
        policy_version="workspace-rapid-v1",
        protected_source={"commit_sha": "7" * 40, "tree_sha": "8" * 40},
    )


def test_effect_identity_requires_canonical_nonempty_set() -> None:
    assert AUTHORIZATION_EVIDENCE_SCHEMA == (
        "bifrost.workspace-release-authorization/v1"
    )
    digest = computed_effects_id(["bifrost.read", "integration.read:graph"])
    assert digest.startswith("sha256:")

    for invalid in (
        [],
        ["integration.read:graph", "bifrost.read"],
        ["bifrost.read", "bifrost.read"],
    ):
        with pytest.raises(ValueError, match="sorted, and unique"):
            computed_effects_id(invalid)

    with pytest.raises(ValueError, match="JSON list"):
        computed_effects_id(("bifrost.read",))  # type: ignore[arg-type]


def test_challenge_binds_governed_manifest_and_every_authority_identity() -> None:
    original = _challenge()
    changed = activation_challenge(
        artifact_id=original["artifact_id"],
        candidate_id=original["candidate_id"],
        workspace_release_id=original["workspace_release_id"],
        prepared_evidence_id=original["prepared_evidence_id"],
        effective_manifest_id=original["effective_manifest_id"],
        governed_manifest_id="sha256:" + "9" * 64,
        effective_registration_manifest_id=original[
            "effective_registration_manifest_id"
        ],
        risk_class=original["risk_class"],
        computed_effects=original["computed_effects"],
        policy_version=original["policy_version"],
        protected_source=original["protected_source"],
    )

    assert original["challenge_id"] != changed["challenge_id"]
    assert validate_activation_challenge(original) == original


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("candidate_id", "sha256:" + "a" * 64),
        ("prepared_evidence_id", "sha256:" + "b" * 64),
        ("workspace_release_id", "sha256:" + "c" * 64),
        ("risk_class", "R1"),
        ("computed_effects_id", "sha256:" + "d" * 64),
        ("policy_version", "changed"),
    ],
)
def test_challenge_rejects_any_noncanonical_mutation(field, replacement) -> None:
    challenge = {**_challenge(), field: replacement}

    with pytest.raises(ValueError, match="not canonical"):
        validate_activation_challenge(challenge)


def test_risk_acknowledgement_is_exact_and_never_claims_effect_execution() -> None:
    challenge = _challenge()
    acknowledgement = risk_acknowledgement(challenge)

    assert acknowledgement["decision"] == RISK_ACKNOWLEDGEMENT_DECISION
    assert acknowledgement["risk_class"] == "R2"
    assert validate_risk_acknowledgement(acknowledgement, challenge) == acknowledgement

    tampered = {**acknowledgement, "risk_class": "R1"}
    with pytest.raises(ValueError, match="does not match"):
        validate_risk_acknowledgement(tampered, challenge)


def test_r0_challenge_requires_canary_and_cannot_be_acknowledged() -> None:
    challenge = _challenge(risk_class="R0", effects=["bifrost.read"])

    assert challenge["required_authorization"] == "reviewed_canary"
    with pytest.raises(ValueError, match="successful reviewed canary"):
        risk_acknowledgement(challenge)


@pytest.mark.parametrize(
    "field,replacement,error",
    [
        ("artifact_id", "not-a-uuid", "artifact ID"),
        ("candidate_id", "sha256:not-a-digest", "candidate ID"),
        ("policy_version", "", "policy version"),
        ("protected_source", [], "protected-main source"),
    ],
)
def test_challenge_builder_rejects_malformed_authority_identity(
    field, replacement, error
) -> None:
    kwargs = {
        "artifact_id": "11111111-1111-1111-1111-111111111111",
        "candidate_id": "sha256:" + "1" * 64,
        "workspace_release_id": "sha256:" + "2" * 64,
        "prepared_evidence_id": "sha256:" + "3" * 64,
        "effective_manifest_id": "sha256:" + "4" * 64,
        "governed_manifest_id": "sha256:" + "5" * 64,
        "effective_registration_manifest_id": "sha256:" + "6" * 64,
        "risk_class": "R2",
        "computed_effects": ["integration.write:halopsa"],
        "policy_version": "workspace-rapid-v1",
        "protected_source": {"commit_sha": "7" * 40, "tree_sha": "8" * 40},
    }
    kwargs[field] = replacement

    with pytest.raises(ValueError, match=error):
        activation_challenge(**kwargs)
