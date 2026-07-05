from __future__ import annotations

from collections.abc import Callable

from src.services.cron_parser import cron_to_human_readable, validate_cron_expression
from src.services.editor.search import _search_content
from src.services.webhooks.protocol import WebhookRequest


def fuzz_editor_search(data: bytes) -> None:
    text = data.decode("utf-8", errors="replace")
    pivot = max(1, min(len(text), 64))
    query = text[:pivot] or "x"
    content = text[pivot:]

    _search_content(content, "fuzz.txt", query, case_sensitive=False, is_regex=False)

    pattern = query[:80]
    try:
        _search_content(content, "fuzz.txt", pattern, case_sensitive=False, is_regex=True)
    except ValueError as exc:
        if "nested quantifiers" not in str(exc) and "exceeds" not in str(exc):
            raise


def fuzz_cron_parser(data: bytes) -> None:
    expression = data.decode("utf-8", errors="replace").strip()
    if not expression:
        expression = "* * * * *"

    is_valid = validate_cron_expression(expression)
    description = cron_to_human_readable(expression)

    assert isinstance(is_valid, bool)
    assert isinstance(description, str)
    assert description


def fuzz_webhook_request(data: bytes) -> None:
    request = WebhookRequest(
        method="POST",
        path="/api/events/webhooks/fuzz",
        headers={"content-type": "application/json"},
        query_params={"source": "fuzz"},
        body=data,
        client_ip="127.0.0.1",
    )

    json_body = request.json_body
    if json_body is not None:
        assert isinstance(json_body, dict)
        assert request.json_body is json_body

    text_body = request.text_body
    assert isinstance(text_body, str)


HARNESS_TARGETS: dict[str, Callable[[bytes], None]] = {
    "cron-parser": fuzz_cron_parser,
    "editor-search": fuzz_editor_search,
    "webhook-request": fuzz_webhook_request,
}


def run_harness(target: str, data: bytes) -> None:
    try:
        harness = HARNESS_TARGETS[target]
    except KeyError as exc:
        names = ", ".join(sorted(HARNESS_TARGETS))
        raise ValueError(f"Unknown fuzz harness '{target}'. Known harnesses: {names}") from exc

    harness(data)
