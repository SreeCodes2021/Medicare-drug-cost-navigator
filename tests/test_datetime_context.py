from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from medicare_navigator.agent.datetime_context import (
    DEFAULT_TIMEZONE,
    add_months,
    build_datetime_context,
    days_remaining_in_contract_year,
    resolve_explicit_start_date,
    resolve_timezone,
    window_days_remaining,
)
from medicare_navigator.agent.prompts import build_navigator_system_prompt


def _freeze_now(monkeypatch, fixed: datetime) -> None:
    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr("medicare_navigator.agent.datetime_context.datetime", _FixedDatetime)


def test_resolve_timezone_defaults_to_chicago():
    zone = resolve_timezone(None)
    assert zone.key == DEFAULT_TIMEZONE


def test_resolve_timezone_invalid_falls_back_to_chicago():
    zone = resolve_timezone("invalid/zone")
    assert zone.key == DEFAULT_TIMEZONE


def test_resolve_timezone_valid_iana_name():
    zone = resolve_timezone("America/New_York")
    assert zone.key == "America/New_York"


def test_build_datetime_context_includes_derived_fields(monkeypatch):
    fixed = datetime(2026, 8, 3, 19, 23, tzinfo=ZoneInfo("America/Chicago"))

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr("medicare_navigator.agent.datetime_context.datetime", _FixedDatetime)

    context = build_datetime_context("America/Chicago")

    assert "America/Chicago" in context
    assert "August 3, 2026" in context
    assert "Calendar year: 2026" in context
    assert "Q3 2026" in context
    assert "Days remaining in 2026: 150" in context
    assert "Never ask the user to confirm today's date" in context


def test_build_navigator_system_prompt_includes_datetime_context():
    prompt = build_navigator_system_prompt("America/Chicago")
    assert "Current date and time" in prompt
    assert "Never ask the user to confirm today's date" in prompt


def test_add_months_clamps_day_of_month_for_shorter_target_month():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_add_months_respects_leap_year():
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)


def test_add_months_rolls_over_year_boundary():
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


def test_add_months_plain_case():
    assert add_months(date(2026, 8, 3), 4) == date(2026, 12, 3)


def test_window_days_remaining_with_no_override_delegates_to_existing_function(monkeypatch):
    fixed = datetime(2026, 8, 3, 19, 23, tzinfo=ZoneInfo("America/Chicago"))

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr("medicare_navigator.agent.datetime_context.datetime", _FixedDatetime)

    assert window_days_remaining(2026, "America/Chicago") == days_remaining_in_contract_year(
        2026, "America/Chicago"
    )
    assert window_days_remaining(2026, "America/Chicago") == 150


def test_window_days_remaining_with_explicit_start_no_end_runs_to_year_end():
    assert window_days_remaining(2026, "America/Chicago", start=date(2026, 9, 1)) == 121


def test_window_days_remaining_custom_end_before_year_end():
    assert (
        window_days_remaining(
            2026, "America/Chicago", start=date(2026, 9, 1), end=date(2026, 12, 1)
        )
        == 91
    )


def test_window_days_remaining_custom_end_beyond_year_end_is_capped():
    """CMS benefit design (deductible/cap) resets each Jan 1 — a window can never correctly
    extend past the current contract year with the benefit data this app has."""
    capped = window_days_remaining(
        2026, "America/Chicago", start=date(2026, 9, 1), end=date(2027, 3, 1)
    )
    uncapped_equivalent = window_days_remaining(2026, "America/Chicago", start=date(2026, 9, 1))
    assert capped == uncapped_equivalent == 121


def test_window_days_remaining_start_before_contract_year_resets_to_jan_first():
    assert window_days_remaining(2026, "America/Chicago", start=date(2025, 6, 1)) == 364


def test_window_days_remaining_start_after_contract_year_is_zero():
    assert window_days_remaining(2026, "America/Chicago", start=date(2027, 1, 1)) == 0


def test_window_days_remaining_start_after_end_is_zero():
    assert (
        window_days_remaining(
            2026, "America/Chicago", start=date(2026, 12, 15), end=date(2026, 12, 1)
        )
        == 0
    )


# resolve_explicit_start_date — the mediator's raw month/day/year extraction is resolved
# here into a concrete date (navigator.py's deterministic insulin path is its only caller).
# Covers §2h's scenario 2 pytest equivalent at the unit level, plus the year-rollover
# boundary and malformed-date input that /quality-test §2h and exploratory-qa's malformed-
# date category ("starting February 30th") exercise live but nothing previously locked down
# offline.


def test_resolve_explicit_start_date_returns_none_when_day_missing():
    assert resolve_explicit_start_date(9, None, None) is None


def test_resolve_explicit_start_date_returns_none_when_month_missing():
    assert resolve_explicit_start_date(None, 1, None) is None


def test_resolve_explicit_start_date_returns_none_when_both_missing():
    assert resolve_explicit_start_date(None, None, None) is None


def test_resolve_explicit_start_date_with_explicit_year_uses_exact_date_no_rollover():
    """An explicit year is copied through verbatim — including a past year, which the
    caller (window_days_remaining) is responsible for zeroing out, not this function."""
    assert resolve_explicit_start_date(9, 1, 2020) == date(2020, 9, 1)


def test_resolve_explicit_start_date_no_year_rolls_forward_when_date_already_passed(
    monkeypatch,
):
    _freeze_now(monkeypatch, datetime(2026, 8, 3, 12, 0, tzinfo=ZoneInfo("America/Chicago")))
    assert resolve_explicit_start_date(3, 1, None, "America/Chicago") == date(2027, 3, 1)


def test_resolve_explicit_start_date_no_year_keeps_current_year_when_still_upcoming(
    monkeypatch,
):
    _freeze_now(monkeypatch, datetime(2026, 8, 3, 12, 0, tzinfo=ZoneInfo("America/Chicago")))
    assert resolve_explicit_start_date(9, 1, None, "America/Chicago") == date(2026, 9, 1)


def test_resolve_explicit_start_date_today_exactly_does_not_roll_forward(monkeypatch):
    """Boundary check on the `< today` comparison: a candidate equal to today must resolve
    to this year, not silently jump a year ahead."""
    _freeze_now(monkeypatch, datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo("America/Chicago")))
    assert resolve_explicit_start_date(9, 1, None, "America/Chicago") == date(2026, 9, 1)


def test_resolve_explicit_start_date_invalid_day_of_month_raises_value_error():
    """Documents current behavior for nonsense phrasing like "starting February 30th":
    the mediator schema has no day-of-month bound (see agent/mediator.py MediatorRewrite),
    so an invalid day reaches date() construction unguarded and raises ValueError here.
    In production this propagates out of the deterministic insulin path to api/app.py's
    `except ValueError -> HTTPException(400)` handler, so it fails loud rather than
    fabricating a bogus window — but it is not the "graceful clarification" the
    exploratory-qa malformed-date category calls for. If that's fixed, update this test."""
    with pytest.raises(ValueError):
        resolve_explicit_start_date(2, 30, 2026)


def test_resolve_explicit_start_date_feb_29_on_non_leap_year_raises_value_error():
    with pytest.raises(ValueError):
        resolve_explicit_start_date(2, 29, 2026)
