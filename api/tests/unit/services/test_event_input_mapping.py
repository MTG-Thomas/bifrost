"""
Unit tests for event input mapping template processing.

Tests the _render_template and _process_input_mapping functions
used when schedule or webhook events trigger workflow subscriptions
with input_mapping configured.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.services.events.processor import _process_input_mapping, _render_template


class TestRenderTemplate:
    """Test template rendering with {{ variable }} expressions."""

    def test_single_variable_preserves_type(self):
        """Single variable templates return the actual value, not a string."""
        context = {"count": 42}
        result = _render_template("{{ count }}", context)
        assert result == 42
        assert isinstance(result, int)

    def test_single_variable_dict(self):
        """Single variable returning a dict preserves the dict type."""
        data = {"key": "value", "nested": {"a": 1}}
        context = {"payload": data}
        result = _render_template("{{ payload }}", context)
        assert result == data
        assert isinstance(result, dict)

    def test_dot_notation_access(self):
        """Dot notation accesses nested dict values."""
        context = {"payload": {"user": {"email": "test@example.com"}}}
        result = _render_template("{{ payload.user.email }}", context)
        assert result == "test@example.com"

    def test_mixed_content_returns_string(self):
        """Mixed content with variables returns a substituted string."""
        context = {"name": "Alice"}
        result = _render_template("Hello {{ name }}!", context)
        assert result == "Hello Alice!"
        assert isinstance(result, str)

    def test_multiple_variables(self):
        """Multiple variables in one template are all substituted."""
        context = {"first": "John", "last": "Doe"}
        result = _render_template("{{ first }} {{ last }}", context)
        assert result == "John Doe"

    def test_unresolved_variable_kept(self):
        """Unresolved variables are kept as-is."""
        context = {"name": "Alice"}
        result = _render_template("{{ unknown }}", context)
        assert result == "{{ unknown }}"

    def test_unresolved_dot_path_kept(self):
        """Unresolved dot paths are kept as-is."""
        context = {"payload": {"user": {}}}
        result = _render_template("{{ payload.user.missing }}", context)
        assert result == "{{ payload.user.missing }}"

    def test_none_value_becomes_empty_string(self):
        """None values become empty strings in mixed content."""
        context = {"value": None}
        result = _render_template("prefix-{{ value }}-suffix", context)
        assert result == "prefix--suffix"

    def test_whitespace_in_braces(self):
        """Various whitespace around variable names is handled."""
        context = {"name": "test"}
        assert _render_template("{{name}}", context) == "test"
        assert _render_template("{{ name }}", context) == "test"
        assert _render_template("{{  name  }}", context) == "test"


class TestProcessInputMapping:
    """Test input mapping processing for event subscriptions."""

    def _make_event(self, data=None, headers=None, received_at=None, schedule_source=None):
        """Helper to create a mock Event."""
        event = MagicMock()
        event.data = data or {}
        event.headers = headers
        event.received_at = received_at or datetime(2026, 2, 5, 9, 0, 0, tzinfo=timezone.utc)

        # Mock event_source and schedule_source
        if schedule_source:
            event.event_source = MagicMock()
            event.event_source.schedule_source = schedule_source
        else:
            event.event_source = MagicMock()
            event.event_source.schedule_source = None

        return event

    def _make_subscription(self):
        """Helper to create a mock EventSubscription."""
        return MagicMock()

    def test_static_values_pass_through(self):
        """Static values in input_mapping are passed through unchanged."""
        event = self._make_event()
        sub = self._make_subscription()

        result = _process_input_mapping(
            input_mapping={"report_type": "daily", "count": 5},
            event=event,
            subscription=sub,
        )
        assert result == {"report_type": "daily", "count": 5}

    def test_scheduled_time_template(self):
        """{{ scheduled_time }} resolves to event received_at ISO format."""
        event = self._make_event(
            received_at=datetime(2026, 2, 5, 9, 0, 0, tzinfo=timezone.utc)
        )
        sub = self._make_subscription()

        result = _process_input_mapping(
            input_mapping={"as_of_date": "{{ scheduled_time }}"},
            event=event,
            subscription=sub,
        )
        assert "2026-02-05" in result["as_of_date"]

    def test_payload_template(self):
        """{{ payload.field }} resolves to event data fields."""
        event = self._make_event(data={"user": {"email": "test@example.com"}})
        sub = self._make_subscription()

        result = _process_input_mapping(
            input_mapping={"user_email": "{{ payload.user.email }}"},
            event=event,
            subscription=sub,
        )
        assert result["user_email"] == "test@example.com"

    def test_cron_expression_template(self):
        """{{ cron_expression }} resolves from schedule source."""
        schedule_source = MagicMock()
        schedule_source.cron_expression = "0 9 * * *"
        event = self._make_event(schedule_source=schedule_source)
        sub = self._make_subscription()

        result = _process_input_mapping(
            input_mapping={"schedule": "{{ cron_expression }}"},
            event=event,
            subscription=sub,
        )
        assert result["schedule"] == "0 9 * * *"

    def test_mixed_static_and_template(self):
        """Input mapping with both static and template values."""
        event = self._make_event(data={"action": "create"})
        sub = self._make_subscription()

        result = _process_input_mapping(
            input_mapping={
                "static_param": "fixed_value",
                "event_action": "{{ payload.action }}",
                "run_time": "{{ scheduled_time }}",
            },
            event=event,
            subscription=sub,
        )
        assert result["static_param"] == "fixed_value"
        assert result["event_action"] == "create"
        assert "2026" in result["run_time"]

    def test_headers_template(self):
        """{{ headers.field }} resolves from event headers."""
        event = self._make_event(headers={"X-Request-Id": "abc123"})
        sub = self._make_subscription()

        result = _process_input_mapping(
            input_mapping={"request_id": "{{ headers.X-Request-Id }}"},
            event=event,
            subscription=sub,
        )
        assert result["request_id"] == "abc123"

    def test_empty_input_mapping(self):
        """Empty input_mapping returns empty dict."""
        event = self._make_event()
        sub = self._make_subscription()

        result = _process_input_mapping(
            input_mapping={},
            event=event,
            subscription=sub,
        )
        assert result == {}
