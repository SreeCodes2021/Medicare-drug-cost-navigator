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

INSULIN_OUT_OF_SCOPE_MESSAGE = (
    "Insulin cost estimates are not supported by this tool. Insulin has a separate statutory "
    "$35/month cap that does not depend on deductible or benefit-phase status, and CMS "
    "publishes it under a different file than the one this estimator uses. Please check your "
    "plan's insulin-specific pricing directly."
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
