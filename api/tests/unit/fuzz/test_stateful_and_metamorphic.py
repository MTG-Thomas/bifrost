from __future__ import annotations

import os

from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from src.services.cron_parser import cron_to_human_readable, validate_cron_expression
from src.services.editor.search import _search_content


def _property_examples(default: int) -> int:
    return int(os.environ.get("BIFROST_FUZZ_EXAMPLES", str(default)))


class CronExpressionStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.minute = "*"
        self.hour = "*"
        self.day = "*"
        self.month = "*"
        self.weekday = "*"

    @rule(minute=st.sampled_from(["*", "0", "15", "30", "45", "*/5", "*/15"]))
    def set_minute(self, minute: str) -> None:
        self.minute = minute

    @rule(hour=st.sampled_from(["*", "0", "9", "12", "17", "*/2", "9-17"]))
    def set_hour(self, hour: str) -> None:
        self.hour = hour

    @rule(day=st.sampled_from(["*", "1", "15", "28"]))
    def set_day(self, day: str) -> None:
        self.day = day

    @rule(month=st.sampled_from(["*", "1", "6", "12"]))
    def set_month(self, month: str) -> None:
        self.month = month

    @rule(weekday=st.sampled_from(["*", "0", "1", "5", "7"]))
    def set_weekday(self, weekday: str) -> None:
        self.weekday = weekday

    @invariant()
    def generated_expression_stays_valid_and_describable(self) -> None:
        expression = " ".join(
            [self.minute, self.hour, self.day, self.month, self.weekday]
        )

        assert validate_cron_expression(expression) is True
        description = cron_to_human_readable(expression)
        assert description
        assert not description.startswith("Invalid CRON")


TestCronExpressionStateMachine = CronExpressionStateMachine.TestCase
TestCronExpressionStateMachine.settings = settings(
    max_examples=_property_examples(100),
    stateful_step_count=20,
)


@given(
    expression=st.sampled_from(
        [
            "* * * * *",
            "*/5 * * * *",
            "0 * * * *",
            "0 9 * * 1",
            "0 0 1 * *",
        ]
    )
)
@settings(max_examples=_property_examples(100))
def test_cron_description_is_stable_under_outer_whitespace(expression: str):
    padded = f" \t{expression}\n"

    assert validate_cron_expression(padded) is True
    assert cron_to_human_readable(padded) == cron_to_human_readable(expression)


@given(
    token=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu"), max_codepoint=127),
        min_size=1,
        max_size=12,
    ),
    prefix=st.text(alphabet="0123456789 _-./", max_size=40),
    suffix=st.text(alphabet="0123456789 _-./", max_size=40),
)
@settings(max_examples=_property_examples(200))
def test_case_insensitive_literal_search_survives_case_transform(
    token: str,
    prefix: str,
    suffix: str,
):
    content = f"{prefix}{token}{suffix}"
    lower_results = _search_content(
        content.lower(),
        "fuzz.txt",
        token.upper(),
        case_sensitive=False,
        is_regex=False,
    )
    upper_results = _search_content(
        content.upper(),
        "fuzz.txt",
        token.lower(),
        case_sensitive=False,
        is_regex=False,
    )

    assert len(lower_results) == len(upper_results)
    assert len(lower_results) >= 1
