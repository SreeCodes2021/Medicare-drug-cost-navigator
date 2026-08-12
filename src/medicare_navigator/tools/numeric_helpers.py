"""Small numeric helpers shared across cost-estimation modules."""

from __future__ import annotations


def unique_or_none(values: list[float | None]) -> float | None:
    """Return the sole non-None value when all present values agree; else None."""
    present = [v for v in values if v is not None]
    if not present:
        return None
    unique = set(present)
    if len(unique) == 1:
        return present[0]
    return None
