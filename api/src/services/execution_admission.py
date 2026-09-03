"""Low-cardinality telemetry for workload admission and backpressure."""

from __future__ import annotations

import logging
from enum import StrEnum

from opentelemetry import metrics

logger = logging.getLogger(__name__)
_admission_counter = None
_admission_wait = None


class AdmissionOutcome(StrEnum):
    ADMITTED = "admitted"
    DEFERRED = "deferred"


def record_admission_decision(
    *,
    workload_class: str,
    admission_policy: str,
    outcome: AdmissionOutcome,
    reason: str,
    wait_seconds: float = 0.0,
) -> None:
    """Emit one normalized decision without job IDs or payload cardinality."""

    global _admission_counter, _admission_wait
    meter = metrics.get_meter(__name__)
    if _admission_counter is None:
        _admission_counter = meter.create_counter(
            "bifrost.execution.admission.decisions",
            unit="1",
            description="Execution admission decisions by workload and reason.",
        )
    if _admission_wait is None:
        _admission_wait = meter.create_histogram(
            "bifrost.execution.admission.wait",
            unit="s",
            description="Time spent waiting at an execution admission boundary.",
        )
    attributes = {
        "workload_class": workload_class,
        "admission_policy": admission_policy,
        "outcome": outcome.value,
        "reason": reason,
    }
    _admission_counter.add(1, attributes)
    _admission_wait.record(max(0.0, wait_seconds), attributes)
    logger.info("execution_admission_decision", extra=attributes)
