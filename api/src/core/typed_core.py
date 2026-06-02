"""Strictly typed core-domain landing zone for Bifrost.

This module intentionally starts small. It gives platform refactors a concrete,
strictly checked import path for pure domain helpers before larger execution,
manifest, or permission rules are migrated out of handlers and service glue.

Rules for this package:
- keep functions deterministic and side-effect free when practical;
- do not import FastAPI, SQLAlchemy sessions, queues, or HTTP clients;
- prefer explicit dataclasses, enums, and Pydantic DTOs over loose dictionaries;
- keep type ignores rare and documented with the invariant being preserved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CoreDecision(StrEnum):
    """Outcome marker for pure core-domain decisions."""

    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class CoreInvariantViolation:
    """Structured reason a core-domain invariant rejected an operation."""

    code: str
    message: str


def require_non_empty_name(value: str, *, field_name: str = "name") -> str:
    """Return a trimmed name or raise ValueError for an empty domain name.

    This is deliberately tiny: it gives the strict core landing zone a real,
    testable helper without changing production behavior elsewhere yet.
    Future refactors should move duplicated name/identity validation here only
    when the caller semantics are known and tested.
    """

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized
