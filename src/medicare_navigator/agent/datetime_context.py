from __future__ import annotations

import calendar
from datetime import date, datetime
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


def add_months(start: date, months: int) -> date:
    """Calendar month addition with day-of-month clamping (Jan 31 + 1 month -> Feb 28/29).

    Deterministic, stdlib-only — the model never computes this; see agent/mediator.py."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    last_day_of_month = calendar.monthrange(year, month)[1]
    day = min(start.day, last_day_of_month)
    return date(year, month, day)


def window_days_remaining(
    contract_year: int,
    tz: str | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
) -> int:
    """Generalizes days_remaining_in_contract_year to an explicit (start, end) window,
    always capped at the contract year's end — CMS benefit design (deductible/cap) resets
    each January 1, so a window can never correctly extend past the current contract year
    with the benefit data this app has. With no start/end, delegates directly to
    days_remaining_in_contract_year so that call site's behavior is untouched."""
    if start is None and end is None:
        return days_remaining_in_contract_year(contract_year, tz)
    zone = resolve_timezone(tz)
    window_start = start if start is not None else datetime.now(zone).date()
    year_end = datetime(contract_year, 12, 31, tzinfo=zone).date()
    window_end = min(end, year_end) if end is not None else year_end
    if window_start.year > contract_year or window_start > window_end:
        return 0
    if window_start.year < contract_year:
        window_start = datetime(contract_year, 1, 1, tzinfo=zone).date()
    return (window_end - window_start).days


def resolve_explicit_start_date(
    month: int | None, day: int | None, year: int | None, tz: str | None = None
) -> date | None:
    """Resolve an explicit month/day (optionally year) mediator extraction into a concrete
    date, applying a fixed, testable roll-forward rule when no year was stated ("starting
    September 1" means the next such date, not a guess) — never a model guess. Returns
    None when month/day aren't both given."""
    if month is None or day is None:
        return None
    if year is not None:
        return date(year, month, day)
    zone = resolve_timezone(tz)
    today = datetime.now(zone).date()
    candidate = date(today.year, month, day)
    if candidate < today:
        return date(today.year + 1, month, day)
    return candidate


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
