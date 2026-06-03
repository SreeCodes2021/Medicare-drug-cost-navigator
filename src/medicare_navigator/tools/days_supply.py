"""Spec Section 4: pricing.DAYS_SUPPLY and beneficiary_cost.DAYS_SUPPLY are different
representations (a raw day count vs. a CMS code). This is the single named mapping —
callers must not inline or repeat this translation at each join site."""

from __future__ import annotations

DAYS_SUPPLY_CODE_MAP: dict[int, int] = {
    30: 1,
    60: 4,
    90: 2,
}


def map_pricing_days_supply_to_code(days_supply: int) -> int | None:
    """Map a raw pricing days-supply value to its beneficiary_cost CODE.

    Returns None for any value outside {30, 60, 90} — the "other" branch from Section 4.
    Callers must not silently coerce an unmapped value to a nearby code.
    """
    return DAYS_SUPPLY_CODE_MAP.get(days_supply)
