"""Pure comparison helpers for Workspace promotion diagnostics."""

from __future__ import annotations

from collections.abc import Iterable

from bifrost.workspace_release import canonical_digest

from src.models.contracts.workspace_promotions import (
    PromotionDiagnostic,
    PromotionDiagnosticChange,
    PromotionDiagnosticDelta,
)

_SEVERITY_RANK = {"info": 0, "warning": 1, "blocker": 2}


def diagnostic_fingerprint(diagnostic: PromotionDiagnostic) -> str:
    """Identify a finding without binding mutable wording or severity."""

    return canonical_digest(
        {
            "schema": "bifrost.workspace-diagnostic-identity/v1",
            "code": diagnostic.code,
            "path": diagnostic.path,
            "subject": (
                diagnostic.subject.model_dump() if diagnostic.subject else None
            ),
        }
    )


def _sort_key(diagnostic: PromotionDiagnostic) -> tuple[str, str, str]:
    return (diagnostic_fingerprint(diagnostic), diagnostic.severity, diagnostic.message)


def _unique_differential(
    diagnostics: Iterable[PromotionDiagnostic], *, label: str
) -> dict[str, PromotionDiagnostic]:
    result: dict[str, PromotionDiagnostic] = {}
    for diagnostic in diagnostics:
        if diagnostic.enforcement != "differential":
            continue
        fingerprint = diagnostic_fingerprint(diagnostic)
        if fingerprint in result:
            raise ValueError(f"duplicate {label} diagnostic identity: {fingerprint}")
        result[fingerprint] = diagnostic
    return result


def compare_promotion_diagnostics(
    *,
    baseline_release_id: str,
    baseline_manifest_id: str,
    affected_paths: Iterable[str],
    baseline: Iterable[PromotionDiagnostic],
    candidate: Iterable[PromotionDiagnostic],
) -> PromotionDiagnosticDelta:
    """Classify candidate diagnostics while keeping absolute gates unconditional."""

    affected = sorted(set(affected_paths))
    affected_set = set(affected)
    baseline_items = list(baseline)
    candidate_items = list(candidate)
    baseline_by_id = _unique_differential(baseline_items, label="baseline")
    candidate_by_id = _unique_differential(candidate_items, label="candidate")

    introduced = [item for item in candidate_items if item.enforcement == "absolute"]
    worsened: list[PromotionDiagnosticChange] = []
    unchanged: list[PromotionDiagnostic] = []
    resolved: list[PromotionDiagnostic] = []
    unrelated_by_id: dict[str, PromotionDiagnostic] = {}

    for fingerprint, item in candidate_by_id.items():
        if item.path is not None and item.path not in affected_set:
            unrelated_by_id[fingerprint] = item
            continue
        previous = baseline_by_id.get(fingerprint)
        if previous is None:
            introduced.append(item)
        elif _SEVERITY_RANK[item.severity] > _SEVERITY_RANK[previous.severity]:
            worsened.append(
                PromotionDiagnosticChange(baseline=previous, candidate=item)
            )
        else:
            unchanged.append(item)

    for fingerprint, item in baseline_by_id.items():
        if item.path is not None and item.path not in affected_set:
            unrelated_by_id.setdefault(fingerprint, item)
        elif fingerprint not in candidate_by_id:
            resolved.append(item)

    worsened.sort(key=lambda change: _sort_key(change.candidate))
    return PromotionDiagnosticDelta(
        baseline_release_id=baseline_release_id,
        baseline_manifest_id=baseline_manifest_id,
        affected_paths=affected,
        introduced=sorted(introduced, key=_sort_key),
        worsened=worsened,
        unchanged=sorted(unchanged, key=_sort_key),
        resolved=sorted(resolved, key=_sort_key),
        unrelated=sorted(unrelated_by_id.values(), key=_sort_key),
    )


def blocking_diagnostics(
    delta: PromotionDiagnosticDelta,
) -> list[PromotionDiagnostic]:
    """Return only absolute, new, or worsened candidate blockers."""

    return sorted(
        [item for item in delta.introduced if item.severity == "blocker"]
        + [
            change.candidate
            for change in delta.worsened
            if change.candidate.severity == "blocker"
        ],
        key=_sort_key,
    )


def validate_diagnostic_delta(
    delta: PromotionDiagnosticDelta,
    *,
    expected_baseline_release_id: str,
    expected_baseline_manifest_id: str,
) -> None:
    """Fail closed when immutable baseline bindings are absent or stale."""

    if delta.baseline_release_id != expected_baseline_release_id:
        raise ValueError(
            "diagnostic baseline release does not match the candidate base"
        )
    if delta.baseline_manifest_id != expected_baseline_manifest_id:
        raise ValueError(
            "diagnostic baseline manifest does not match the candidate base"
        )
