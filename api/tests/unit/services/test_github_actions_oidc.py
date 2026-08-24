from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.services.github_actions_oidc import (
    GITHUB_ACTIONS_ISSUER,
    WORKSPACE_SOURCE_RELEASE_AUDIENCE,
    GitHubActionsOIDCError,
    authenticate_workspace_source_release_producer,
)

REPOSITORY = "MTG-Thomas/bifrost-workspace"
WORKFLOW_REF = (
    "MTG-Thomas/bifrost-workspace/.github/workflows/"
    "declare-workspace-source-release.yml@refs/heads/main"
)
ORGANIZATION_ID = "9f65129e-0e8a-4a3b-a56a-27d831bd7ab1"
SOURCE_SHA = "a" * 40


def _settings():
    return SimpleNamespace(
        workspace_source_release_oidc_repository=REPOSITORY,
        workspace_source_release_oidc_repository_id=1197464564,
        workspace_source_release_oidc_repository_owner_id=87775189,
        workspace_source_release_oidc_workflow_ref=WORKFLOW_REF,
        workspace_source_release_oidc_organization_id=ORGANIZATION_ID,
    )


def _token(*, overrides: dict[str, object] | None = None):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    claims: dict[str, object] = {
        "aud": WORKSPACE_SOURCE_RELEASE_AUDIENCE,
        "exp": now + timedelta(minutes=5),
        "iat": now,
        "iss": GITHUB_ACTIONS_ISSUER,
        "jti": "test-jti",
        "nbf": now - timedelta(seconds=5),
        "repository": REPOSITORY,
        "repository_id": "1197464564",
        "repository_owner_id": "87775189",
        "ref": "refs/heads/main",
        "ref_type": "branch",
        "event_name": "push",
        "sha": SOURCE_SHA,
        "workflow_ref": WORKFLOW_REF,
        "run_id": "123456",
    }
    claims.update(overrides or {})
    token = jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    public_key = private_key.public_key()
    jwks = {
        "keys": [
            jwt.algorithms.RSAAlgorithm.to_jwk(public_key, as_dict=True)
            | {"kid": "test-key", "alg": "RS256", "use": "sig"}
        ]
    }
    return token, jwks


@pytest.mark.asyncio
async def test_accepts_exact_protected_main_push_identity() -> None:
    token, jwks = _token()

    producer = await authenticate_workspace_source_release_producer(
        token,
        settings=_settings(),
        jwks=jwks,
    )

    assert str(producer.organization_id) == ORGANIZATION_ID
    assert producer.source_commit_sha == SOURCE_SHA
    assert producer.repository == REPOSITORY
    assert producer.workflow_ref == WORKFLOW_REF
    assert producer.run_id == "123456"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("repository_id", "999"),
        ("repository_owner_id", "999"),
        ("ref", "refs/heads/feature"),
        ("event_name", "pull_request"),
        ("workflow_ref", f"{REPOSITORY}/.github/workflows/other.yml@refs/heads/main"),
    ],
)
async def test_rejects_identity_outside_pinned_policy(claim: str, value: str) -> None:
    token, jwks = _token(overrides={claim: value})

    with pytest.raises(GitHubActionsOIDCError, match=claim):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )


@pytest.mark.asyncio
async def test_rejects_wrong_audience() -> None:
    token, jwks = _token(overrides={"aud": "not-bifrost"})

    with pytest.raises(GitHubActionsOIDCError, match="signature or claim"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )
