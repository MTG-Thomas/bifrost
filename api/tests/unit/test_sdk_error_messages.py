"""Tests for SDK error message improvement (raise_for_status_with_detail)."""

import httpx
import pytest

from bifrost.client import raise_for_status_with_detail


def _make_response(status_code: int, json_body: dict | None = None, text_body: str = "") -> httpx.Response:
    """Build a fake httpx.Response with the given status and body."""
    if json_body is not None:
        content = httpx.Response(
            status_code=status_code,
            request=httpx.Request("POST", "https://example.com/api/test"),
            json=json_body,
        )
        return content

    return httpx.Response(
        status_code=status_code,
        request=httpx.Request("POST", "https://example.com/api/test"),
        text=text_body,
    )


class TestRaiseForStatusWithDetail:
    def test_success_response_does_not_raise(self):
        response = _make_response(200, json_body={"ok": True})
        raise_for_status_with_detail(response)  # should not raise

    def test_422_includes_message_field(self):
        response = _make_response(
            422,
            json_body={"error": "validation_error", "message": "limit: Input should be less than or equal to 1000"},
        )
        with pytest.raises(httpx.HTTPStatusError, match="limit: Input should be less than or equal to 1000"):
            raise_for_status_with_detail(response)

    def test_422_falls_back_to_error_field(self):
        response = _make_response(422, json_body={"error": "validation_error"})
        with pytest.raises(httpx.HTTPStatusError, match="validation_error"):
            raise_for_status_with_detail(response)

    def test_422_non_json_body_falls_back_gracefully(self):
        response = _make_response(422, text_body="Bad request")
        with pytest.raises(httpx.HTTPStatusError):
            raise_for_status_with_detail(response)

    def test_500_includes_message(self):
        response = _make_response(500, json_body={"message": "Internal server error"})
        with pytest.raises(httpx.HTTPStatusError, match="Internal server error"):
            raise_for_status_with_detail(response)

    def test_empty_json_body_falls_back(self):
        response = _make_response(400, json_body={})
        with pytest.raises(httpx.HTTPStatusError):
            raise_for_status_with_detail(response)

    def test_message_preferred_over_error(self):
        response = _make_response(
            422,
            json_body={"error": "validation_error", "message": "specific detail here"},
        )
        with pytest.raises(httpx.HTTPStatusError, match="specific detail here"):
            raise_for_status_with_detail(response)

    def test_exception_contains_request_and_response(self):
        response = _make_response(422, json_body={"message": "bad input"})
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            raise_for_status_with_detail(response)
        assert exc_info.value.response is response
        assert exc_info.value.request is response.request
