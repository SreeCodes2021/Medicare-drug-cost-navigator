"""CMS Part D annual benefit parameters (statutory OOP cap by contract year)."""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from medicare_navigator.config import settings

_DEFAULT_ANNUAL_OOP_CAP: dict[int, float] = {
    2025: 2000.0,
    2026: 2100.0,
}


@lru_cache(maxsize=1)
def _load_benefit_params() -> dict[str, Any]:
    path: Path = settings.config_dir / "benefit_params.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def annual_oop_cap(contract_year: int) -> float:
    """Statutory Part D annual out-of-pocket maximum for the contract year."""
    data = _load_benefit_params()
    caps = data.get("annual_oop_cap") or {}
    if contract_year in caps:
        return float(caps[contract_year])
    if contract_year in _DEFAULT_ANNUAL_OOP_CAP:
        return _DEFAULT_ANNUAL_OOP_CAP[contract_year]
    # Nearest known year for forward/backward compatibility.
    known = sorted(_DEFAULT_ANNUAL_OOP_CAP)
    if contract_year < known[0]:
        return _DEFAULT_ANNUAL_OOP_CAP[known[0]]
    return _DEFAULT_ANNUAL_OOP_CAP[known[-1]]


def effective_tier_cost_ceiling(
    tier_cost_max: float | None,
    contract_year: int,
) -> float | None:
    """CMS tier COST_MAX capped by the statutory annual OOP maximum."""
    if tier_cost_max is None:
        return None
    return min(tier_cost_max, annual_oop_cap(contract_year))


def cap_fill_copay(
    copay: float,
    tier_cost_max: float | None,
    contract_year: int,
) -> float:
    ceiling = effective_tier_cost_ceiling(tier_cost_max, contract_year)
    if ceiling is None:
        return copay
    return min(copay, ceiling)


def remaining_oop_headroom(ytd_oop_spend: float, contract_year: int) -> float:
    return max(0.0, annual_oop_cap(contract_year) - ytd_oop_spend)


def project_period_budget(
    *,
    ytd_oop_spend: float,
    days_supply: int,
    cost_low: float | None,
    cost_high: float | None,
    contract_year: int,
    period_days: float,
) -> tuple[float, float, float | None, float | None, int]:
    """Project OOP over `period_days` at the same fill cadence (simplified; same phase/copay).

    Returns (annual_oop_cap, remaining_headroom, budget_cost_low, budget_cost_high, fills).
    """
    cap = annual_oop_cap(contract_year)
    headroom = remaining_oop_headroom(ytd_oop_spend, contract_year)
    if cost_low is None or period_days <= 0:
        return cap, headroom, None, None, 0
    fills = max(0, math.ceil(period_days / max(days_supply, 1)))
    high = cost_high if cost_high is not None else cost_low
    raw_low = ytd_oop_spend + fills * cost_low
    raw_high = ytd_oop_spend + fills * high
    return cap, headroom, min(cap, raw_low), min(cap, raw_high), fills


def project_annual_budget(
    *,
    ytd_oop_spend: float,
    days_supply: int,
    cost_low: float | None,
    cost_high: float | None,
    contract_year: int,
) -> tuple[float, float, float | None, float | None]:
    """Project year-end OOP if the same fill cadence continues (simplified; same phase/copay).

    Returns (annual_oop_cap, remaining_headroom, budget_cost_low, budget_cost_high).
    """
    cap = annual_oop_cap(contract_year)
    headroom = remaining_oop_headroom(ytd_oop_spend, contract_year)
    if cost_low is None:
        return cap, headroom, None, None
    fills_per_year = 365.0 / max(days_supply, 1)
    high = cost_high if cost_high is not None else cost_low
    raw_low = ytd_oop_spend + fills_per_year * cost_low
    raw_high = ytd_oop_spend + fills_per_year * high
    return cap, headroom, min(cap, raw_low), min(cap, raw_high)


def project_remaining_year_budget(
    *,
    ytd_oop_spend: float,
    days_supply: int,
    cost_low: float | None,
    cost_high: float | None,
    contract_year: int,
    days_remaining: int,
) -> tuple[float, float, float | None, float | None, int, int]:
    """Project OOP for fills from today through the contract year end.

    Returns (annual_oop_cap, remaining_headroom, budget_cost_low, budget_cost_high,
    days_remaining, fills). Budget costs are future fills only, capped by remaining headroom.
    """
    cap = annual_oop_cap(contract_year)
    headroom = remaining_oop_headroom(ytd_oop_spend, contract_year)
    if cost_low is None or days_remaining <= 0:
        return cap, headroom, None, None, max(days_remaining, 0), 0
    fills = max(0, math.ceil(days_remaining / max(days_supply, 1)))
    high = cost_high if cost_high is not None else cost_low
    raw_low = fills * cost_low
    raw_high = fills * high
    return (
        cap,
        headroom,
        min(headroom, raw_low),
        min(headroom, raw_high),
        days_remaining,
        fills,
    )
