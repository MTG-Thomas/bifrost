from datetime import datetime, timezone
from uuid import uuid4

from src.models.contracts.executions import (
    ExecutionAttemptHistory,
    ExecutionAttemptPublic,
    WorkflowExecution,
)
from src.models.enums import ExecutionStatus


def test_historical_execution_reports_unavailable_attempt_coverage() -> None:
    execution = WorkflowExecution(
        execution_id=str(uuid4()),
        workflow_name="legacy",
        executed_by=str(uuid4()),
        executed_by_name="Legacy User",
        status=ExecutionStatus.SUCCESS,
        created_at=datetime.now(timezone.utc),
        input_data={},
    )

    assert execution.attempt_history.coverage == "legacy_unavailable"
    assert execution.attempt_history.attempts == []


def test_attempt_public_contract_never_contains_claim_token() -> None:
    attempt = ExecutionAttemptPublic(
        attempt_id=uuid4(),
        attempt_number=1,
        status="succeeded",
        phase="terminal",
        policy_version="workflow-attempt/v1",
        created_at=datetime.now(timezone.utc),
        claimed_at=datetime.now(timezone.utc),
    )
    history = ExecutionAttemptHistory(coverage="recorded", attempts=[attempt])

    payload = history.model_dump(mode="json")
    assert payload["coverage"] == "recorded"
    assert "claim_token" not in payload["attempts"][0]
