import pytest

from src.services.cron_parser import (
    validate_cron_expression,
    is_cron_expression_valid,
    cron_to_human_readable,
)


class TestValidateCronExpression:

    @pytest.mark.parametrize("expression", [
        "0 9 * * *",
        "*/30 * * * *",
        "0 0 * * 0",
        "* * * * *",
        "0 9 1 1 *",
        "0 9,17 * * *",
        "0 9-17 * * 1-5",
    ])
    def test_valid_expressions(self, expression):
        assert validate_cron_expression(expression) is True

    @pytest.mark.parametrize("expression,reason", [
        ("* * * * * *", "6 fields"),
        ("invalid", "single word"),
        ("", "empty string"),
        ("0 9 * *", "4 fields"),
        ("0 9 * * * * *", "7 fields"),
    ])
    def test_invalid_expressions(self, expression, reason):
        assert validate_cron_expression(expression) is False

    def test_invalid_field_values(self):
        assert validate_cron_expression("99 99 99 99 99") is False

    def test_non_numeric_garbage(self):
        assert validate_cron_expression("a b c d e") is False


class TestCronToHumanReadable:

    def test_every_n_minutes(self):
        assert cron_to_human_readable("*/5 * * * *") == "Every 5 minutes"

    def test_every_30_minutes(self):
        assert cron_to_human_readable("*/30 * * * *") == "Every 30 minutes"

    def test_every_minute(self):
        assert cron_to_human_readable("* * * * *") == "Every minute"

    def test_every_hour(self):
        assert cron_to_human_readable("0 * * * *") == "Every hour at minute 0"

    def test_every_n_hours(self):
        assert cron_to_human_readable("0 */3 * * *") == "Every 3 hours"

    def test_every_2_hours(self):
        assert cron_to_human_readable("0 */2 * * *") == "Every 2 hours"

    def test_daily_at_specific_time(self):
        assert cron_to_human_readable("0 9 * * *") == "every day at 09:00"

    def test_daily_at_afternoon(self):
        assert cron_to_human_readable("30 14 * * *") == "every day at 14:30"

    def test_monday(self):
        assert cron_to_human_readable("0 9 * * 1") == "every Monday at 09:00"

    def test_sunday_day_0(self):
        assert cron_to_human_readable("0 9 * * 0") == "every Sunday at 09:00"

    def test_monthly_first(self):
        result = cron_to_human_readable("0 0 1 * *")
        assert result == "on the 1st of every month at 00:00"

    def test_multiple_hours(self):
        result = cron_to_human_readable("0 9,17 * * *")
        assert "09:00" in result
        assert "17:00" in result

    def test_hour_range(self):
        result = cron_to_human_readable("0 9-17 * * *")
        assert "between" in result

    def test_invalid_expression_returns_message(self):
        assert cron_to_human_readable("invalid") == "Invalid CRON expression"

    def test_six_fields_returns_invalid(self):
        assert cron_to_human_readable("* * * * * *") == "Invalid CRON expression"

    def test_empty_returns_invalid(self):
        assert cron_to_human_readable("") == "Invalid CRON expression"

    @pytest.mark.parametrize("dow,day_name", [
        ("2", "Tuesday"),
        ("3", "Wednesday"),
        ("4", "Thursday"),
        ("5", "Friday"),
        ("6", "Saturday"),
        ("7", "Sunday"),
    ])
    def test_weekdays(self, dow, day_name):
        result = cron_to_human_readable(f"0 9 * * {dow}")
        assert day_name in result

    def test_specific_day_and_month(self):
        result = cron_to_human_readable("0 9 15 6 *")
        assert "day 15" in result
        assert "month 6" in result

    def test_specific_day_every_month(self):
        result = cron_to_human_readable("0 9 15 * *")
        assert "day 15" in result
        assert "every month" in result


class TestIsCronExpressionValid:

    @pytest.mark.parametrize("expression", [
        "0 9 * * *",
        "*/5 * * * *",
        "* * * * *",
        "0 0 1 * *",
        "0 9 * * 1",
    ])
    def test_valid_expressions_return_true(self, expression):
        assert is_cron_expression_valid(expression) is True

    @pytest.mark.parametrize("expression", [
        "invalid",
        "",
        "* * * * * *",
        "0 9 * *",
    ])
    def test_invalid_expressions_return_false(self, expression):
        assert is_cron_expression_valid(expression) is False

    def test_delegates_to_validate_first(self):
        assert is_cron_expression_valid("not a cron") is False

    def test_valid_cron_that_converts_to_human_readable(self):
        assert is_cron_expression_valid("0 9 * * *") is True
        result = cron_to_human_readable("0 9 * * *")
        assert result not in ("Invalid CRON expression", "Invalid CRON expression format")
