"""Fail-closed sanitization for durably persisted execution variables."""

from __future__ import annotations

import re
from typing import Any

from src.core.secret_string import REDACTED, SecretString


_SENSITIVE_KEY_TOKENS = frozenset(
    {
        "authorization",
        "command",
        "cookie",
        "credential",
        "password",
        "passwd",
        "powershell",
        "privatekey",
        "script",
        "secret",
        "token",
    }
)
_SENSITIVE_COMPACT_KEYS = frozenset(
    {
        "apikey",
        "clientsecret",
        "code",
        "connectionstring",
        "privatekey",
        "powershellcode",
        "pythoncode",
        "scriptcode",
        "sourcecode",
    }
)

_SCRIPT_CONTENT_PATTERNS = (
    re.compile(r"(?m)^\s*#!"),
    re.compile(r"(?m)^\s*(?:async\s+)?def\s+\w+\s*\("),
    re.compile(r"(?m)^\s*(?:import|from)\s+[A-Za-z_]"),
    re.compile(r"(?m)^\s*(?:CREATE\s+LOGIN|ALTER\s+SERVER\s+ROLE)\b", re.IGNORECASE),
    re.compile(r"\$(?:ErrorActionPreference|ProgressPreference)\b", re.IGNORECASE),
    re.compile(r"\b(?:ConvertFrom-Base64String|Invoke-[a-z]+|New-Object)\b", re.IGNORECASE),
    re.compile(
        r"(?im)^\s*(?:curl|wget|pwsh|powershell|bash|sh|cmd(?:\.exe)?|python(?:3)?)\b"
    ),
)


def _key_tokens(key: Any) -> set[str]:
    """Split snake/kebab/camel keys without substring false positives."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    parts = re.findall(r"[A-Za-z0-9]+", text.casefold())
    return set(parts) | ({"".join(parts)} if parts else set())


def _is_sensitive_key(key: Any) -> bool:
    tokens = _key_tokens(key)
    compact_key = "".join(re.findall(r"[A-Za-z0-9]+", str(key))).casefold()
    return bool(
        tokens & _SENSITIVE_KEY_TOKENS
        or compact_key in _SENSITIVE_COMPACT_KEYS
    )


def _looks_like_script(value: str) -> bool:
    """Conservatively recognize executable text even under a generic key."""
    return any(pattern.search(value) for pattern in _SCRIPT_CONTENT_PATTERNS)


def _sanitize_execution_variables(value: Any, active_container_ids: set[int]) -> Any:
    """Recursively sanitize one value while rejecting active-path cycles."""
    if isinstance(value, SecretString):
        return REDACTED
    if isinstance(value, (dict, list, tuple, set)):
        container_id = id(value)
        if container_id in active_container_ids:
            return REDACTED
        active_container_ids.add(container_id)
        try:
            if isinstance(value, dict):
                return {
                    key: REDACTED
                    if _is_sensitive_key(key)
                    else _sanitize_execution_variables(item, active_container_ids)
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [
                    _sanitize_execution_variables(item, active_container_ids)
                    for item in value
                ]
            if isinstance(value, tuple):
                return tuple(
                    _sanitize_execution_variables(item, active_container_ids)
                    for item in value
                )
            return {
                _sanitize_execution_variables(item, active_container_ids)
                for item in value
            }
        finally:
            active_container_ids.remove(container_id)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return REDACTED
    if isinstance(value, str) and _looks_like_script(value):
        return REDACTED
    if not isinstance(value, (str, int, float, bool, type(None))):
        return f"<{type(value).__name__}>"
    return value


def sanitize_execution_variables(value: Any) -> Any:
    """Remove secret-bearing/script content before execution-history persistence.

    Variable names remain available for debugging, but values under sensitive
    names and executable text are replaced. The traversal is recursive so a
    script cannot bypass the boundary by being nested in a payload container.
    """
    return _sanitize_execution_variables(value, set())
