"""Narrow GitHub Actions OIDC authentication for Workspace merge declarations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import jwt

from src.config import Settings

GITHUB_ACTIONS_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_ACTIONS_JWKS_URL = f"{GITHUB_ACTIONS_ISSUER}/.well-known/jwks"
WORKSPACE_SOURCE_RELEASE_AUDIENCE = "bifrost-workspace-source-release"


class GitHubActionsOIDCError(ValueError):
    """The producer token is absent, invalid, or outside the pinned trust policy."""


@dataclass(frozen=True)
class WorkspaceSourceReleaseProducer:
    organization_id: UUID
    source_commit_sha: str
    repository: str
    workflow_ref: str
    run_id: str


def _required_setting(value: str | int | None, name: str) -> str:
    if value is None or str(value).strip() == "":
        raise GitHubActionsOIDCError(
            f"Workspace source-release OIDC is not configured: {name} is required"
        )
    return str(value)


async def authenticate_workspace_source_release_producer(
    token: str,
    *,
    settings: Settings,
    jwks: dict[str, Any] | None = None,
) -> WorkspaceSourceReleaseProducer:
    """Verify one push-to-main producer token against immutable GitHub claims."""
    repository = _required_setting(
        settings.workspace_source_release_oidc_repository,
        "BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_REPOSITORY",
    )
    repository_id = _required_setting(
        settings.workspace_source_release_oidc_repository_id,
        "BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_REPOSITORY_ID",
    )
    owner_id = _required_setting(
        settings.workspace_source_release_oidc_repository_owner_id,
        "BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_REPOSITORY_OWNER_ID",
    )
    workflow_ref = _required_setting(
        settings.workspace_source_release_oidc_workflow_ref,
        "BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_WORKFLOW_REF",
    )
    organization_id_raw = _required_setting(
        settings.workspace_source_release_oidc_organization_id,
        "BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_ORGANIZATION_ID",
    )
    try:
        organization_id = UUID(organization_id_raw)
    except ValueError as exc:
        raise GitHubActionsOIDCError(
            "BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_ORGANIZATION_ID must be a UUID"
        ) from exc

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise GitHubActionsOIDCError("GitHub Actions OIDC token is malformed") from exc
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
        raise GitHubActionsOIDCError(
            "GitHub Actions OIDC token must use RS256 with a key identifier"
        )

    if jwks is None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(GITHUB_ACTIONS_JWKS_URL)
                response.raise_for_status()
                jwks = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubActionsOIDCError(
                "GitHub Actions OIDC signing keys are unavailable"
            ) from exc

    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(keys, list):
        raise GitHubActionsOIDCError("GitHub Actions OIDC key set is invalid")
    matching_key = next(
        (
            key
            for key in keys
            if isinstance(key, dict) and key.get("kid") == header["kid"]
        ),
        None,
    )
    if matching_key is None:
        raise GitHubActionsOIDCError(
            "GitHub Actions OIDC signing key is not recognized"
        )

    try:
        signing_key = jwt.PyJWK.from_dict(matching_key).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=WORKSPACE_SOURCE_RELEASE_AUDIENCE,
            issuer=GITHUB_ACTIONS_ISSUER,
            options={
                "require": [
                    "aud",
                    "exp",
                    "iat",
                    "iss",
                    "jti",
                    "nbf",
                    "repository",
                    "repository_id",
                    "repository_owner_id",
                    "ref",
                    "ref_type",
                    "event_name",
                    "sha",
                    "workflow_ref",
                    "run_id",
                ]
            },
        )
    except jwt.InvalidTokenError as exc:
        raise GitHubActionsOIDCError(
            "GitHub Actions OIDC token failed signature or claim validation"
        ) from exc

    expected = {
        "repository": repository,
        "repository_id": repository_id,
        "repository_owner_id": owner_id,
        "ref": "refs/heads/main",
        "ref_type": "branch",
        "event_name": "push",
        "workflow_ref": workflow_ref,
    }
    mismatches = [
        claim
        for claim, expected_value in expected.items()
        if str(claims.get(claim)) != expected_value
    ]
    if mismatches:
        raise GitHubActionsOIDCError(
            "GitHub Actions OIDC token is outside the source-release trust policy: "
            + ", ".join(sorted(mismatches))
        )
    source_commit_sha = str(claims["sha"])
    if len(source_commit_sha) != 40 or any(
        char not in "0123456789abcdef" for char in source_commit_sha
    ):
        raise GitHubActionsOIDCError(
            "GitHub Actions OIDC token has an invalid source commit SHA"
        )
    return WorkspaceSourceReleaseProducer(
        organization_id=organization_id,
        source_commit_sha=source_commit_sha,
        repository=repository,
        workflow_ref=workflow_ref,
        run_id=str(claims["run_id"]),
    )
