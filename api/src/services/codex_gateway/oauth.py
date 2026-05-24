"""OAuth onboarding helpers for Codex Gateway upstream accounts."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any


class CodexAuthCacheError(ValueError):
    """Raised when a Codex auth cache payload cannot be safely imported."""


@dataclass(frozen=True)
class ParsedCodexAuthCache:
    """Token material and safe metadata extracted from a Codex auth cache."""

    access_token: str | None
    refresh_token: str | None
    upstream_subject: str
    upstream_email: str | None
    upstream_workspace_id: str | None
    access_token_expires_at: datetime | None
    scopes: list[str]


def parse_codex_auth_cache(auth_cache: dict[str, Any]) -> ParsedCodexAuthCache:
    """Parse a user's own Codex auth cache without leaking token values."""
    if not isinstance(auth_cache, dict):
        raise CodexAuthCacheError("Codex auth cache must be a JSON object.")

    token_source = _first_mapping(
        auth_cache.get("tokens"),
        auth_cache.get("openai"),
        auth_cache.get("chatgpt"),
        auth_cache,
    )
    account_source = _first_mapping(
        auth_cache.get("account"),
        auth_cache.get("user"),
        auth_cache.get("profile"),
        auth_cache,
    )

    access_token = _first_string(
        token_source.get("access_token"),
        token_source.get("OPENAI_ACCESS_TOKEN"),
        token_source.get("codex_access_token"),
    )
    refresh_token = _first_string(
        token_source.get("refresh_token"),
        token_source.get("OPENAI_REFRESH_TOKEN"),
        token_source.get("codex_refresh_token"),
    )
    if not access_token and not refresh_token:
        raise CodexAuthCacheError(
            "Codex auth cache does not contain usable token material."
        )

    subject = _first_string(
        account_source.get("sub"),
        account_source.get("subject"),
        account_source.get("user_id"),
        account_source.get("account_id"),
    )
    email = _first_string(
        account_source.get("email"),
        account_source.get("upstream_email"),
    )
    workspace_id = _first_string(
        account_source.get("workspace_id"),
        account_source.get("workspace"),
        account_source.get("organization_id"),
    )

    if subject is None:
        subject = _unknown_subject(access_token or refresh_token or "")

    return ParsedCodexAuthCache(
        access_token=access_token,
        refresh_token=refresh_token,
        upstream_subject=subject,
        upstream_email=email,
        upstream_workspace_id=workspace_id,
        access_token_expires_at=_parse_expiry(token_source),
        scopes=_parse_scopes(token_source.get("scope") or token_source.get("scopes")),
    )


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _parse_expiry(token_source: dict[str, Any]) -> datetime | None:
    raw = (
        token_source.get("expires_at")
        or token_source.get("expiry")
        or token_source.get("expiration")
    )
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    exp = token_source.get("expires_at_epoch") or token_source.get("expires_at_unix")
    if isinstance(exp, (int, float)):
        return datetime.fromtimestamp(exp, tz=timezone.utc)
    return None


def _parse_scopes(value: Any) -> list[str]:
    if isinstance(value, str):
        return [scope for scope in value.split() if scope]
    if isinstance(value, list):
        return [scope for scope in value if isinstance(scope, str) and scope]
    return []


def _unknown_subject(token: str) -> str:
    digest = hashlib.sha256(token.encode()).hexdigest()[:16]
    return f"unknown:{digest}"
