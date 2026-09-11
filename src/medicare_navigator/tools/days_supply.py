"""Spec Section 4: pricing.DAYS_SUPPLY and beneficiary_cost.DAYS_SUPPLY are different
representations (a raw day count vs. a CMS code). This is the single named mapping —
callers must not inline or repeat this translation at each join site."""

from __future__ import annotations

import re
from typing import Any

DAYS_SUPPLY_CODE_MAP: dict[int, int] = {
    30: 1,
    60: 4,
    90: 2,
}

STANDARD_DAYS_SUPPLIES = frozenset(DAYS_SUPPLY_CODE_MAP)

_EXPLICIT_DAYS_SUPPLY_RE = re.compile(
    r"(?:^|\s)(\d+)\s*[- ]?days?\s+supply\b"
    r"|(?:for\s+)?a\s+(\d+)\s*[- ]day\s+supply\b"
    r"|days?\s+supply\s+(?:of\s+)?(\d+)\b",
    re.I,
)

_ESTIMATE_TOOL_NAMES = frozenset({"estimate_drug_cost", "estimate_drug_cost_all_channels"})


def extract_explicit_days_supply(message: str) -> int | None:
    """Return 30/60/90 when the user names a fill size; otherwise None."""
    for match in _EXPLICIT_DAYS_SUPPLY_RE.finditer(message):
        days_str = match.group(1) or match.group(2) or match.group(3)
        if not days_str:
            continue
        days = int(days_str)
        if days in STANDARD_DAYS_SUPPLIES:
            return days
    return None


def coerce_estimate_days_supply(
    *,
    message: str,
    filter_days_supply: int | None = None,
    last_tool_call: dict[str, Any] | None = None,
) -> int:
    """Resolve days_supply for estimate tools — never trust the LLM to default correctly.

    Priority: UI/filter override, explicit user phrase, prior estimate in this thread,
    then the statutory 30-day default.
    """
    if filter_days_supply is not None:
        return filter_days_supply
    explicit = extract_explicit_days_supply(message)
    if explicit is not None:
        return explicit
    if last_tool_call and last_tool_call.get("name") in _ESTIMATE_TOOL_NAMES:
        last_days = (last_tool_call.get("arguments") or {}).get("days_supply")
        if last_days is not None:
            return int(last_days)
    return 30


def map_pricing_days_supply_to_code(days_supply: int) -> int | None:
    """Map a raw pricing days-supply value to its beneficiary_cost CODE.

    Returns None for any value outside {30, 60, 90} — the "other" branch from Section 4.
    Callers must not silently coerce an unmapped value to a nearby code.
    """
    return DAYS_SUPPLY_CODE_MAP.get(days_supply)


def invalid_days_supply_message(days_supply: int) -> str | None:
    """Return a user-facing error when days_supply cannot be estimated."""
    if days_supply <= 0:
        return (
            f"A {days_supply}-day supply isn't valid for estimating. Please use a standard "
            "30-, 60-, or 90-day supply."
        )
    return None
