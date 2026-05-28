"""Tests for the DLQ operational CLI helpers."""

from src.jobs.dlq_cli import decode_message, _describe


class FakePoisonMessage:
    body = b'{"execution_id":"abc"}'
    message_id = "abc"
    correlation_id = "corr"
    headers = {
        "x-idempotency-key": "abc",
        "x-retry-count": 3,
        "x-replayed-count": 1,
        "x-origin-queue": "workflow-executions",
    }


def test_decode_message_handles_valid_json():
    assert decode_message(b'{"ok": true}') == {"ok": True}


def test_decode_message_handles_malformed_body():
    assert decode_message(b"{not-json") == "{not-json"


def test_describe_includes_operational_metadata():
    row = _describe("workflow-executions", FakePoisonMessage())

    assert row["poison_queue"] == "workflow-executions-poison"
    assert row["idempotency_key"] == "abc"
    assert row["retry_count"] == 3
    assert row["replay_count"] == 1
    assert row["body"] == {"execution_id": "abc"}
