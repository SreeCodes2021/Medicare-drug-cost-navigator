from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from medicare_navigator.config import settings

DEFAULT_TIMEZONE = "America/Chicago"


def resolve_timezone(tz: str | None) -> ZoneInfo:
    """Return a valid IANA timezone, falling back to the configured default."""
    candidate = (tz or settings.default_timezone or DEFAULT_TIMEZONE).strip()
    try:
        return ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def _calendar_quarter(month: int) -> int:
    return (month - 1) // 3 + 1


def _days_remaining_in_year(now: datetime) -> int:
    year_end = datetime(now.year, 12, 31, tzinfo=now.tzinfo)
    return (year_end.date() - now.date()).days


def days_remaining_in_contract_year(contract_year: int, tz: str | None = None) -> int:
    """Days from today (inclusive) through Dec 31 of the contract year."""
    zone = resolve_timezone(tz)
    now = datetime.now(zone)
    if now.year > contract_year:
        return 0
    if now.year < contract_year:
        return (
            datetime(contract_year, 12, 31, tzinfo=zone).date()
            - datetime(contract_year, 1, 1, tzinfo=zone).date()
        ).days
    return _days_remaining_in_year(now)


def build_datetime_context(tz: str | None = None) -> str:
    """Build a runtime date/time block for the Navigator system prompt."""
    zone = resolve_timezone(tz)
    now = datetime.now(zone)
    quarter = _calendar_quarter(now.month)
    days_remaining = _days_remaining_in_year(now)
    formatted = (
        f"{now.strftime('%A, %B')} {now.day}, {now.year}, "
        f"{now.strftime('%I').lstrip('0') or '12'}:{now.strftime('%M %p %Z')}"
    )

    return (
        f"Current date and time (user timezone {zone.key}): {formatted}.\n"
        f"Calendar year: {now.year}. Current calendar quarter: Q{quarter} {now.year}. "
        f"Days remaining in {now.year}: {days_remaining}.\n"
        'Use this as authoritative "today" for relative-date questions '
        '(e.g. "rest of the year", "remaining months"). '
        "Never ask the user to confirm today's date."
    )
