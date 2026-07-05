from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.models.enums import ExecutionStatus
from src.sdk.context import ExecutionContext, Organization
from src.services.execution import engine


def test_coerce_params_to_type_hints_handles_scalars_optional_and_failures():
    def workflow(count: int, ratio: float, enabled: bool, optional: Optional[int], untouched):
        return None

    result = engine._coerce_params_to_type_hints(
        workflow,
        {
            "count": "3",
            "ratio": "2.5",
            "enabled": "yes",
            "optional": "7",
            "bad_int": "not-coerced",
            "untouched": "value",
        },
    )

    assert result == {
        "count": 3,
        "ratio": 2.5,
        "enabled": True,
        "optional": 7,
        "bad_int": "not-coerced",
        "untouched": "value",
    }


def test_scrub_outputs_redacts_context_secret_values():
    context = ExecutionContext(
        user_id="user-1",
        email="user@example.com",
        name="User One",
        scope="org-1",
        organization=Organization(id="org-1", name="Org One"),
        is_platform_admin=False,
        is_function_key=False,
    )
    context._register_dynamic_secret("secret-value")

    result, variables, logs, error = engine._scrub_outputs(
        context,
        result={"token": "secret-value"},
        variables={"seen": "secret-value"},
        logs=[{"message": "used secret-value"}],
        error_message="failed with secret-value",
    )

    assert result == {"token": "[REDACTED]"}
    assert variables == {"seen": "[REDACTED]"}
    assert logs == [{"message": "used [REDACTED]"}]
    assert error == "failed with [REDACTED]"


def test_build_cached_result_normalizes_expiry_and_marks_cached():
    start = datetime.now(timezone.utc)

    result = engine._build_cached_result(
        "exec-1",
        {"data": {"value": 1}, "expires_at": "2026-07-05T12:00:00"},
        start,
    )

    assert result.execution_id == "exec-1"
    assert result.status is ExecutionStatus.SUCCESS
    assert result.result == {"value": 1}
    assert result.cached is True
    assert result.cache_expires_at == "2026-07-05T12:00:00Z"
    assert result.duration_ms >= 0
