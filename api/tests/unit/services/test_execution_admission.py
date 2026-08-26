"""Contracts for normalized admission telemetry."""

from unittest.mock import MagicMock, patch

from src.services import execution_admission
from src.services.execution_admission import (
    AdmissionOutcome,
    record_admission_decision,
)


def test_admission_metrics_use_only_bounded_dimensions() -> None:
    counter = MagicMock()
    histogram = MagicMock()
    meter = MagicMock()
    meter.create_counter.return_value = counter
    meter.create_histogram.return_value = histogram
    execution_admission._admission_counter = None
    execution_admission._admission_wait = None

    with patch.object(execution_admission.metrics, "get_meter", return_value=meter):
        record_admission_decision(
            workload_class="interactive_workflow",
            admission_policy="workflow_process_pool",
            outcome=AdmissionOutcome.DEFERRED,
            reason="memory_pressure",
            wait_seconds=-1,
        )

    attributes = counter.add.call_args.args[1]
    assert attributes == {
        "workload_class": "interactive_workflow",
        "admission_policy": "workflow_process_pool",
        "outcome": "deferred",
        "reason": "memory_pressure",
    }
    assert "job_id" not in attributes
    histogram.record.assert_called_once_with(0.0, attributes)
