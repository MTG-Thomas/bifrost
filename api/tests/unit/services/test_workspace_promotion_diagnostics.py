import pytest
from pydantic import ValidationError
from src.models.contracts.workspace_promotions import (
    PromotionDiagnostic,
    PromotionDiagnosticSubject,
)
from src.services.workspace_promotion_diagnostics import (
    blocking_diagnostics,
    compare_promotion_diagnostics,
    diagnostic_fingerprint,
    validate_diagnostic_delta,
)

BASE_RELEASE = "sha256:" + "a" * 64
BASE_MANIFEST = "sha256:" + "b" * 64


def finding(
    key: str,
    *,
    severity: str = "blocker",
    path: str = "features/sharepoint/read.py",
    message: str | None = None,
    enforcement: str = "differential",
) -> PromotionDiagnostic:
    return PromotionDiagnostic(
        code="unresolved_repo_import",
        severity=severity,
        message=message or key,
        path=path,
        subject=PromotionDiagnosticSubject(kind="import", key=key),
        enforcement=enforcement,
    )


def compare(
    *, baseline=(), candidate=(), affected_paths=("features/sharepoint/read.py",)
):
    return compare_promotion_diagnostics(
        baseline_release_id=BASE_RELEASE,
        baseline_manifest_id=BASE_MANIFEST,
        affected_paths=affected_paths,
        baseline=baseline,
        candidate=candidate,
    )


def test_fingerprint_ignores_wording_and_severity_but_binds_subject() -> None:
    first = finding("modules.missing", severity="warning", message="old wording")
    renamed = finding("modules.missing", severity="blocker", message="new wording")
    different = finding("modules.other")

    assert diagnostic_fingerprint(first) == diagnostic_fingerprint(renamed)
    assert diagnostic_fingerprint(first) != diagnostic_fingerprint(different)


def test_differential_diagnostic_requires_structured_subject() -> None:
    with pytest.raises(ValidationError, match="stable subject"):
        PromotionDiagnostic(
            code="unresolved_repo_import",
            severity="blocker",
            message="missing",
            path="features/demo.py",
            enforcement="differential",
        )


def test_delta_classifies_findings_and_is_stably_sorted() -> None:
    unchanged = finding("unchanged")
    worsened_before = finding("worsened", severity="warning")
    worsened_after = finding("worsened", severity="blocker")
    resolved = finding("resolved")
    introduced = finding("introduced")
    unrelated = finding("dmarc", path="features/dmarc/report.py")
    absolute = finding("local-run", enforcement="absolute")

    delta = compare(
        baseline=[resolved, unrelated, worsened_before, unchanged],
        candidate=[unchanged, worsened_after, introduced, unrelated, absolute],
    )

    assert {item.subject.key for item in delta.introduced} == {
        "introduced",
        "local-run",
    }
    assert [item.candidate.subject.key for item in delta.worsened] == ["worsened"]
    assert [item.subject.key for item in delta.unchanged] == ["unchanged"]
    assert [item.subject.key for item in delta.resolved] == ["resolved"]
    assert [item.subject.key for item in delta.unrelated] == ["dmarc"]
    assert delta.affected_paths == ["features/sharepoint/read.py"]
    assert [diagnostic_fingerprint(item) for item in delta.introduced] == sorted(
        diagnostic_fingerprint(item) for item in delta.introduced
    )


def test_only_absolute_new_and_worsened_blockers_block() -> None:
    unchanged = finding("unchanged")
    unrelated = finding("dmarc", path="features/dmarc/report.py")
    new_warning = finding("warning", severity="warning")
    absolute = finding("absolute", enforcement="absolute")
    worsened_before = finding("worsened", severity="warning")
    worsened_after = finding("worsened")

    delta = compare(
        baseline=[unchanged, unrelated, worsened_before],
        candidate=[
            unchanged,
            unrelated,
            new_warning,
            absolute,
            worsened_after,
        ],
    )

    assert {item.subject.key for item in blocking_diagnostics(delta)} == {
        "absolute",
        "worsened",
    }


def test_duplicate_differential_identity_fails_closed() -> None:
    with pytest.raises(ValueError, match="duplicate candidate diagnostic identity"):
        compare(candidate=[finding("same"), finding("same", message="duplicate")])


@pytest.mark.parametrize(
    ("release_id", "manifest_id", "message"),
    [
        ("sha256:" + "c" * 64, BASE_MANIFEST, "baseline release"),
        (BASE_RELEASE, "sha256:" + "d" * 64, "baseline manifest"),
    ],
)
def test_baseline_binding_mismatch_fails_closed(
    release_id: str, manifest_id: str, message: str
) -> None:
    delta = compare()

    with pytest.raises(ValueError, match=message):
        validate_diagnostic_delta(
            delta,
            expected_baseline_release_id=release_id,
            expected_baseline_manifest_id=manifest_id,
        )
