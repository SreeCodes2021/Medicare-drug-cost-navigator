"""Insulin cost estimation: flat $35/30-day statutory cap (Inflation Reduction Act), via
CMS's Insulin Beneficiary Cost File — a separate pipeline from the tiered/deductible
cost-share path in estimate_drug_cost.py's _compute_channel_costs.

No deductible ever applies to insulin, so there's no phase branching here beyond the
catastrophic $0 override (all covered Part D drugs, insulin included, drop to $0 once
the annual OOP cap is crossed). copay_amt_*_insln (via InsulinBeneficiaryCostRepository)
is the only field read — CMS's parallel coin_amt_*_insln field does not reliably match
plans' real coinsurance rates (empirically verified against the full CMS release; see
disclaimers.INSULIN_STATUTORY_CAP_CAVEAT), so it's never used to compute a dollar figure.

estimate_drug_cost.py imports this module, so this module must never import back from
estimate_drug_cost.py (its _EstimateContext/_ChannelComputation are private to it) —
that would be a circular import. Everything here takes primitive/typed args and returns
its own dataclass instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from medicare_navigator.storage.repository import (
    BasicDrugsFormularyRecord,
    InsulinBeneficiaryCostRepository,
)
from medicare_navigator.tools.numeric_helpers import unique_or_none


@dataclass
class InsulinChannelComputation:
    ndc_costs: list[float] = field(default_factory=list)
    tiers_matched: list[int] = field(default_factory=list)
    any_coinsurance_excluded: bool = False
    plan_copay: float | None = None
    plan_coinsurance_pct: float | None = None
    applied_copay: float | None = None
    applied_coinsurance_pct: float | None = None


def has_insulin_cost_data(
    plan_key: str,
    matches: list[BasicDrugsFormularyRecord],
    days_supply_code: int,
    repo: InsulinBeneficiaryCostRepository | None = None,
) -> bool:
    """True if any matched tier has an insulin cost-share row (any channel) for this
    plan/fill-size. False means the narrow insulin_out_of_scope fallback applies — a
    genuine CMS data gap for this plan, not "insulin unsupported"."""
    repo = repo or InsulinBeneficiaryCostRepository()
    tiers = {m.tier for m in matches}
    return any(repo.has_any(plan_key, tier, days_supply_code) for tier in tiers)


def compute_insulin_channel_costs(
    *,
    plan_key: str,
    matches: list[BasicDrugsFormularyRecord],
    days_supply_code: int | None,
    pharmacy_channel: str,
    is_catastrophic: bool,
    repo: InsulinBeneficiaryCostRepository | None = None,
) -> InsulinChannelComputation:
    """Drop-in analog of estimate_drug_cost.py's _compute_channel_costs for insulin: no
    deductible/coverage-level branching at all (insulin is statutorily deductible-exempt
    in every phase but catastrophic), just a direct capped-copay lookup per matched tier
    plus the catastrophic $0 override.

    plan_copay always reflects the CMS-published capped figure, even in catastrophic
    phase (so a user can see what it would be); applied_copay/ndc_costs reflect the
    actual charge (0 in catastrophic phase). A channel with no CMS row (not offered)
    stays unpriced in both fields, in every phase — never fabricated as $0.
    """
    repo = repo or InsulinBeneficiaryCostRepository()
    tiers_matched: list[int] = []
    ndc_costs: list[float] = []
    plan_copays: list[float | None] = []
    applied_copays: list[float | None] = []

    for tier in sorted({m.tier for m in matches}):
        tiers_matched.append(tier)
        copay = repo.get_cost_share(
            plan_key, tier, days_supply_code, pharmacy_channel=pharmacy_channel
        )
        if copay is None:
            plan_copays.append(None)
            applied_copays.append(None)
            continue
        plan_copays.append(round(copay, 2))
        fill_copay = 0.0 if is_catastrophic else copay
        applied_copays.append(round(fill_copay, 2))
        ndc_costs.append(round(fill_copay, 2))

    return InsulinChannelComputation(
        ndc_costs=ndc_costs,
        tiers_matched=tiers_matched,
        any_coinsurance_excluded=False,
        plan_copay=unique_or_none(plan_copays),
        plan_coinsurance_pct=None,
        applied_copay=unique_or_none(applied_copays),
        applied_coinsurance_pct=None,
    )
