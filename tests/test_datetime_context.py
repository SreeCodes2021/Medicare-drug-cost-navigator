from datetime import datetime
from zoneinfo import ZoneInfo

from medicare_navigator.agent.datetime_context import (
    DEFAULT_TIMEZONE,
    build_datetime_context,
    resolve_timezone,
)
from medicare_navigator.agent.prompts import build_navigator_system_prompt


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
