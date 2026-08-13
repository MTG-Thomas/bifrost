"""Typed source declarations used by Workspace promotion policy.

The declarations in this module are metadata only.  They describe effects and
bounds to the promotion planner; they do not, by themselves, enforce a limit at
runtime.  Promotion policy must independently verify any bound labelled as
enforced before relying on it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Any, Literal, TypeAlias

WorkflowEffectKind: TypeAlias = Literal[
    "bifrost.read",
    "bifrost.write",
    "integration.read",
    "integration.write",
    "network.read",
    "network.write",
    "filesystem.read",
    "filesystem.write",
    "process.execute",
    "dynamic_code.execute",
]

WORKFLOW_EFFECT_KINDS = frozenset(
    {
        "bifrost.read",
        "bifrost.write",
        "integration.read",
        "integration.write",
        "network.read",
        "network.write",
        "filesystem.read",
        "filesystem.write",
        "process.execute",
        "dynamic_code.execute",
    }
)


@dataclass(frozen=True, slots=True)
class WorkflowEffect:
    """One externally observable effect a Workspace executable may perform."""

    kind: WorkflowEffectKind
    target: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str):
            raise TypeError("Workflow effect kind must be a string")
        if self.kind not in WORKFLOW_EFFECT_KINDS:
            allowed = ", ".join(sorted(WORKFLOW_EFFECT_KINDS))
            raise ValueError(
                f"Unsupported workflow effect kind {self.kind!r}; expected one of: {allowed}"
            )
        if self.target is not None:
            if not isinstance(self.target, str):
                raise TypeError("Workflow effect target must be a string or None")
            if not self.target or self.target != self.target.strip():
                raise ValueError("Workflow effect target must be a non-empty trimmed string")
            if len(self.target) > 256 or not self.target.isprintable():
                raise ValueError(
                    "Workflow effect target must be printable and at most 256 characters"
                )
        if self.kind.startswith("integration.") and self.target is None:
            raise ValueError(f"Workflow effect {self.kind!r} requires an integration target")


@dataclass(frozen=True, slots=True)
class WorkflowBounds:
    """Finite limits declared for a Workspace executable.

    All values are positive integers.  Omitted fields are deliberately absent
    limits rather than implicit defaults.
    """

    max_duration_seconds: int | None = None
    max_external_calls: int | None = None
    max_records_read: int | None = None
    max_records_written: int | None = None
    max_output_rows: int | None = None
    max_output_bytes: int | None = None
    max_pages: int | None = None

    def __post_init__(self) -> None:
        populated = False
        for declaration_field in fields(self):
            value = getattr(self, declaration_field.name)
            if value is None:
                continue
            populated = True
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{declaration_field.name} must be a positive integer")
            if value <= 0:
                raise ValueError(f"{declaration_field.name} must be greater than zero")
        if not populated:
            raise ValueError("Workflow bounds must declare at least one limit")


WorkflowEffectInput: TypeAlias = WorkflowEffect | Mapping[str, Any]
WorkflowBoundsInput: TypeAlias = WorkflowBounds | Mapping[str, Any]


def normalize_workflow_effects(
    value: Sequence[WorkflowEffectInput] | None,
) -> tuple[WorkflowEffect, ...] | None:
    """Validate and normalize decorator effect declarations."""

    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("effects must be a sequence of WorkflowEffect values or mappings")

    normalized: list[WorkflowEffect] = []
    seen: set[tuple[str, str | None]] = set()
    for index, item in enumerate(value):
        if isinstance(item, WorkflowEffect):
            effect = item
        elif isinstance(item, Mapping):
            unknown = set(item) - {"kind", "target"}
            if unknown:
                raise ValueError(
                    f"effects[{index}] contains unknown fields: {', '.join(sorted(unknown))}"
                )
            if "kind" not in item:
                raise ValueError(f"effects[{index}] must include kind")
            kind = item["kind"]
            if not isinstance(kind, str):
                raise TypeError(f"effects[{index}].kind must be a string")
            effect = WorkflowEffect(kind=kind, target=item.get("target"))  # type: ignore[arg-type]
        else:
            raise TypeError(
                f"effects[{index}] must be a WorkflowEffect or mapping, got {type(item).__name__}"
            )

        key = (effect.kind, effect.target)
        if key in seen:
            raise ValueError(f"effects[{index}] duplicates {effect.kind!r} for {effect.target!r}")
        seen.add(key)
        normalized.append(effect)

    return tuple(normalized)


def normalize_workflow_bounds(
    value: WorkflowBoundsInput | None,
    *,
    field_name: str,
) -> WorkflowBounds | None:
    """Validate and normalize one decorator bounds declaration."""

    if value is None:
        return None
    if isinstance(value, WorkflowBounds):
        return value
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a WorkflowBounds value or mapping")

    allowed = {declaration_field.name for declaration_field in fields(WorkflowBounds)}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(
            f"{field_name} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    return WorkflowBounds(**dict(value))
