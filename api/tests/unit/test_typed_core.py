import pytest

from src.core.typed_core import CoreDecision, CoreInvariantViolation, require_non_empty_name


def test_require_non_empty_name_trims_value() -> None:
    assert require_non_empty_name("  Acme  ") == "Acme"


def test_require_non_empty_name_rejects_blank_value() -> None:
    with pytest.raises(ValueError, match="display_name must not be empty"):
        require_non_empty_name("   ", field_name="display_name")


def test_core_decision_values_are_stable() -> None:
    assert CoreDecision.ALLOW.value == "allow"
    assert CoreDecision.DENY.value == "deny"


def test_core_invariant_violation_is_immutable() -> None:
    violation = CoreInvariantViolation(code="empty_name", message="Name is required")

    assert violation.code == "empty_name"
    assert violation.message == "Name is required"

    with pytest.raises(AttributeError):
        violation.code = "changed"  # type: ignore[misc]
