"""Verbatim caveat/disclaimer strings from docs/navigator-implementation-spec.md Sections 5-7.

Single source of truth so tool code and tests never re-type spec language independently —
these strings must reach the end user unmodified (see estimate_drug_cost.py and the
verbatim-caveat guardrail in guardrails/citations.py).
"""

from __future__ import annotations

BUG2_CAVEAT = (
    "This estimate assumes the deductible-phase determination is based on your reported YTD "
    "spend and this plan's per-tier deductible rule as published by CMS. Some plans exempt "
    "certain tiers from the deductible; if your actual pharmacy charge differs from this "
    "estimate, your plan's tier-specific deductible treatment is the most likely reason. "
    "Confirm with your plan."
)

BUG4_CAVEAT = (
    "COINSURANCE NOT CALCULATED — CONTACT INSURER. CMS record layout does not confirm the "
    "dollar base against which the published coinsurance percentage is applied. Computing a "
    "dollar figure here would risk presenting an unverified number as a firm cost estimate."
)

BUG6_MESSAGE = (
    "PLAN_SUPPRESSED_YN = 'Y' for this plan/period. CMS has suppressed this plan's pharmacy "
    "data for data-quality or reliability reasons. We cannot compute or display a cost "
    "estimate from this plan's records — please contact the plan directly."
)

INSULIN_STATUTORY_CAP_CAVEAT = (
    "Federal law (Inflation Reduction Act) caps your cost-sharing for this insulin product at "
    "$35 per 30-day supply (scaled for 60/90-day fills), with no deductible ever applying — this "
    "estimate reflects that cap directly from CMS's insulin-specific pricing file. That file also "
    "publishes a coinsurance-style field for this plan/tier, but it does not reliably match plans' "
    "real coinsurance rates, so it was not used to compute this figure; the copay-based amount "
    "shown is the authoritative one."
)

INSULIN_OUT_OF_SCOPE_MESSAGE = (
    "This plan's CMS insulin-specific pricing file has no published cost-share record for this "
    "product's tier and fill size, so a confirmed dollar estimate isn't available. By federal law "
    "your cost-sharing for a covered insulin product is capped at $35 per 30-day supply with no "
    "deductible — contact your plan to confirm your exact copay for this fill."
)

CATASTROPHIC_PHASE_NOTE = (
    "Your reported year-to-date out-of-pocket spend meets or exceeds the CMS annual Part D "
    "out-of-pocket maximum for this contract year. This fill is estimated using catastrophic "
    "coverage cost-sharing (COVERAGE_LEVEL 3 in CMS data), which is typically $0 for covered "
    "drugs on the regular formulary."
)

NO_COST_SHARE_DATA_MESSAGE = (
    "This plan's CMS-published cost-share file does not include a matching record for this "
    "drug's tier, benefit phase, and days-supply combination, so no dollar estimate could be "
    "computed. Contact your plan or pharmacist for the actual cost."
)


def bug5_caveat(*, matched_ndc_count: int, same_tier: bool, tiers: list[int]) -> str:
    if same_tier:
        tier = tiers[0] if tiers else "?"
        return (
            f"This estimate is based on {matched_ndc_count} formulary NDCs for this drug, all "
            f"Tier {tier} — the price range reflects manufacturer/pricing variation only, not "
            "different cost-share rules."
        )
    tier_list = ", ".join(str(t) for t in sorted(set(tiers)))
    return (
        f"This estimate is based on {matched_ndc_count} formulary NDCs for this drug across "
        f"different tiers ({tier_list}) — your actual cost depends on which specific product "
        "your pharmacy fills."
    )


def bug5b_message(*, requested_days_supply: int, max_allowed_days_supply: int) -> str:
    return (
        f"This plan's quantity limit does not permit a {requested_days_supply}-day supply in a "
        f"single fill. The maximum fill size this plan allows is a {max_allowed_days_supply}-day "
        "supply."
    )


def unmapped_days_supply_caveat(*, days_supply: int, has_cost: bool) -> str:
    if has_cost:
        return (
            f"A {days_supply}-day supply does not match this plan's standard 30/60/90-day "
            "cost-share codes; the estimate below reflects ingredient cost only — cost-sharing "
            "(copay/coinsurance) could not be determined for this fill size."
        )
    return (
        f"A {days_supply}-day supply does not match this plan's standard 30/60/90-day "
        "cost-share codes, and no ingredient-cost or cost-sharing data could be found for this "
        "fill size either. No dollar estimate could be computed — contact your plan or "
        "pharmacist."
    )


def pa_st_caveat(*, prior_authorization: bool, step_therapy: bool) -> str:
    if prior_authorization and step_therapy:
        requirement = "prior authorization and step therapy"
    elif prior_authorization:
        requirement = "prior authorization"
    else:
        requirement = "step therapy"
    return (
        f"This drug requires {requirement} on this plan before it will be covered at this "
        "cost-share. Your pharmacy or prescriber will need to complete this before the "
        "estimate below applies."
    )
