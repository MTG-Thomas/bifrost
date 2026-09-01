from __future__ import annotations

import pytest
from shared.workspace_effects import (
    WorkflowBounds,
    WorkflowEffect,
    normalize_workflow_bounds,
    normalize_workflow_effects,
)


def test_effects_preserve_undeclared_and_explicitly_empty_states() -> None:
    assert normalize_workflow_effects(None) is None
    assert normalize_workflow_effects([]) == ()


def test_effects_normalize_typed_and_mapping_declarations() -> None:
    declared = normalize_workflow_effects(
        [
            WorkflowEffect(kind="bifrost.read", target="tickets"),
            {"kind": "integration.read", "target": "Microsoft Graph"},
        ]
    )

    assert declared == (
        WorkflowEffect(kind="bifrost.read", target="tickets"),
        WorkflowEffect(kind="integration.read", target="Microsoft Graph"),
    )


@pytest.mark.parametrize(
    ("declaration", "error_type", "message"),
    [
        ([{"kind": "integration.read"}], ValueError, "requires an integration target"),
        ([{"kind": "email.send"}], ValueError, "Unsupported workflow effect kind"),
        ([{"kind": "bifrost.read", "scope": "org"}], ValueError, "unknown fields"),
        ([{"target": "tickets"}], ValueError, "must include kind"),
        (["bifrost.read"], TypeError, "must be a WorkflowEffect or mapping"),
        ("bifrost.read", TypeError, "effects must be a sequence"),
    ],
)
def test_effects_reject_malformed_declarations(
    declaration: object,
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=message):
        normalize_workflow_effects(declaration)  # type: ignore[arg-type]


def test_effects_reject_duplicates_instead_of_silently_deduplicating() -> None:
    with pytest.raises(ValueError, match="duplicates"):
        normalize_workflow_effects(
            [
                {"kind": "bifrost.read", "target": "tickets"},
                {"kind": "bifrost.read", "target": "tickets"},
            ]
        )


def test_bounds_normalize_to_typed_positive_limits() -> None:
    bounds = normalize_workflow_bounds(
        {
            "max_duration_seconds": 30,
            "max_external_calls": 4,
            "max_records_read": 500,
            "max_output_rows": 100,
        },
        field_name="enforced_bounds",
    )

    assert bounds == WorkflowBounds(
        max_duration_seconds=30,
        max_external_calls=4,
        max_records_read=500,
        max_output_rows=100,
    )


@pytest.mark.parametrize(
    ("declaration", "error_type", "message"),
    [
        ({}, ValueError, "at least one limit"),
        ({"max_pages": 0}, ValueError, "greater than zero"),
        ({"max_pages": True}, TypeError, "positive integer"),
        ({"max_pages": "3"}, TypeError, "positive integer"),
        ({"max_rows": 3}, ValueError, "unknown fields"),
        (3, TypeError, "must be a WorkflowBounds value or mapping"),
    ],
)
def test_bounds_reject_malformed_declarations(
    declaration: object,
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=message):
        normalize_workflow_bounds(
            declaration,  # type: ignore[arg-type]
            field_name="requested_bounds",
        )
