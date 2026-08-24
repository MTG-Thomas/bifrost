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
    workspace_source_release_producer_configured,
    workspace_source_release_tracking_expected,
)

REPOSITORY = "MTG-Thomas/bifrost-workspace"
WORKFLOW_REF = (
    "MTG-Thomas/bifrost-workspace/.github/workflows/"
    "declare-workspace-source-release.yml@refs/heads/main"
)
ORGANIZATION_ID = "9f65129e-0e8a-4a3b-a56a-27d831bd7ab1"
SOURCE_SHA = "a" * 40
PRODUCER_SHA = "b" * 40
TRIGGERING_RUN_ID = "987654"
TRIGGERING_RUN_ATTEMPT = 3
DECLARATION_DIGEST = "c" * 64
WORKFLOW_RUN_AUDIENCE = (
    f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_run:{TRIGGERING_RUN_ID}:{SOURCE_SHA}"
)
WORKFLOW_DISPATCH_AUDIENCE = (
    f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:"
    f"{TRIGGERING_RUN_ID}:{TRIGGERING_RUN_ATTEMPT}:{SOURCE_SHA}:"
    f"{DECLARATION_DIGEST}"
)


def _settings():
    return SimpleNamespace(
        workspace_source_release_oidc_repository=REPOSITORY,
        workspace_source_release_oidc_repository_id=1197464564,
        workspace_source_release_oidc_repository_owner_id=87775189,
        workspace_source_release_oidc_workflow_ref=WORKFLOW_REF,
        workspace_source_release_oidc_organization_id=ORGANIZATION_ID,
    )


def test_configuration_state_distinguishes_absent_partial_and_ready() -> None:
    assert workspace_source_release_tracking_expected(SimpleNamespace()) is False
    assert workspace_source_release_producer_configured(SimpleNamespace()) is False

    partial = SimpleNamespace(
        workspace_source_release_oidc_repository=REPOSITORY,
    )
    assert workspace_source_release_tracking_expected(partial) is True
    assert workspace_source_release_producer_configured(partial) is False

    assert workspace_source_release_tracking_expected(_settings()) is True
    assert workspace_source_release_producer_configured(_settings()) is True


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
        "actor": "MTG-Thomas",
        "actor_id": "87775189",
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
    assert producer.oidc_commit_sha == SOURCE_SHA
    assert producer.repository == REPOSITORY
    assert producer.workflow_ref == WORKFLOW_REF
    assert producer.run_id == "123456"
    assert producer.event_name == "push"
    assert producer.triggering_workflow_run_id is None
    assert producer.actor == "MTG-Thomas"
    assert producer.actor_id == "87775189"


@pytest.mark.asyncio
async def test_accepts_workflow_run_bound_to_triggering_ci_head_sha() -> None:
    token, jwks = _token(
        overrides={
            "aud": WORKFLOW_RUN_AUDIENCE,
            "event_name": "workflow_run",
            "sha": PRODUCER_SHA,
        }
    )

    producer = await authenticate_workspace_source_release_producer(
        token,
        settings=_settings(),
        jwks=jwks,
    )

    assert producer.source_commit_sha == SOURCE_SHA
    assert producer.oidc_commit_sha == PRODUCER_SHA
    assert producer.workflow_ref == WORKFLOW_REF
    assert producer.event_name == "workflow_run"
    assert producer.triggering_workflow_run_id == TRIGGERING_RUN_ID
    assert producer.actor == "MTG-Thomas"
    assert producer.actor_id == "87775189"


@pytest.mark.asyncio
async def test_accepts_workflow_dispatch_bound_to_successful_ci_head_sha() -> None:
    token, jwks = _token(
        overrides={
            "aud": WORKFLOW_DISPATCH_AUDIENCE,
            "event_name": "workflow_dispatch",
            "sha": PRODUCER_SHA,
            "actor": "github-actions[bot]",
            "actor_id": "41898282",
        }
    )

    producer = await authenticate_workspace_source_release_producer(
        token,
        settings=_settings(),
        jwks=jwks,
    )

    assert producer.source_commit_sha == SOURCE_SHA
    assert producer.oidc_commit_sha == PRODUCER_SHA
    assert producer.event_name == "workflow_dispatch"
    assert producer.triggering_workflow_run_id == TRIGGERING_RUN_ID
    assert producer.triggering_workflow_run_attempt == TRIGGERING_RUN_ATTEMPT
    assert producer.declaration_digest == DECLARATION_DIGEST
    assert producer.actor == "github-actions[bot]"
    assert producer.actor_id == "41898282"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"actor": None, "actor_id": None},
        {"actor": None},
        {"actor_id": None},
        {"actor": "MTG-Thomas", "actor_id": "87775189"},
        {"actor": "github-actions[bot]", "actor_id": "87775189"},
    ],
)
async def test_rejects_workflow_dispatch_without_exact_bot_actor(
    overrides: dict[str, object],
) -> None:
    token, jwks = _token(
        overrides={
            "aud": WORKFLOW_DISPATCH_AUDIENCE,
            "event_name": "workflow_dispatch",
            "sha": PRODUCER_SHA,
        }
        | overrides
    )

    with pytest.raises(GitHubActionsOIDCError, match="actor"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )


@pytest.mark.asyncio
async def test_rejects_workflow_run_with_unbound_fixed_audience() -> None:
    token, jwks = _token(overrides={"event_name": "workflow_run"})

    with pytest.raises(GitHubActionsOIDCError, match="exact-bound"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )


@pytest.mark.asyncio
async def test_rejects_push_with_workflow_run_audience() -> None:
    token, jwks = _token(overrides={"aud": WORKFLOW_RUN_AUDIENCE})

    with pytest.raises(GitHubActionsOIDCError, match="push producer"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_name", "audience"),
    [
        ("push", WORKFLOW_DISPATCH_AUDIENCE),
        ("workflow_run", WORKFLOW_DISPATCH_AUDIENCE),
        ("workflow_dispatch", WORKSPACE_SOURCE_RELEASE_AUDIENCE),
        ("workflow_dispatch", WORKFLOW_RUN_AUDIENCE),
    ],
)
async def test_rejects_event_and_audience_crossovers(
    event_name: str, audience: str
) -> None:
    token, jwks = _token(overrides={"aud": audience, "event_name": event_name})

    with pytest.raises(GitHubActionsOIDCError, match=f"{event_name} producer"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "audience",
    [
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_run:0:{SOURCE_SHA}",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_run:123:{SOURCE_SHA.upper()}",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_run:123",
    ],
)
async def test_rejects_malformed_workflow_run_audience(audience: str) -> None:
    token, jwks = _token(overrides={"aud": audience, "event_name": "workflow_run"})

    with pytest.raises(GitHubActionsOIDCError, match="invalid source-release audience"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "audience",
    [
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:0:1:{SOURCE_SHA}",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:123:0:{SOURCE_SHA}",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:123:1:{SOURCE_SHA.upper()}:{DECLARATION_DIGEST}",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:123:1:{SOURCE_SHA}",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:123:1:{SOURCE_SHA}:BAD",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:123:{SOURCE_SHA}",
        f"{WORKSPACE_SOURCE_RELEASE_AUDIENCE}:workflow_dispatch:123",
    ],
)
async def test_rejects_malformed_workflow_dispatch_audience(audience: str) -> None:
    token, jwks = _token(overrides={"aud": audience, "event_name": "workflow_dispatch"})

    with pytest.raises(GitHubActionsOIDCError, match="invalid source-release audience"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )


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

    with pytest.raises(GitHubActionsOIDCError, match="invalid source-release audience"):
        await authenticate_workspace_source_release_producer(
            token,
            settings=_settings(),
            jwks=jwks,
        )
