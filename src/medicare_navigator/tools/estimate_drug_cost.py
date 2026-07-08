"""Implements docs/navigator-implementation-spec.md Section 3's 8-step pipeline as one
deterministic function. Consolidated (rather than several LLM-chained tool calls) so the
hard-stop and ordering requirements (suppressed-plan check first; days-supply mapping before
any pricing/beneficiary_cost join; insulin routing) can never be skipped or misordered by an
LLM's tool-call sequencing."""

from __future__ import annotations

from math import ceil

from medicare_navigator.ingestion.manifest import get_as_of, get_source_id
from medicare_navigator.models.response import DrugCostEstimate
from medicare_navigator.models.tool_result import ToolResult, ToolStatus
from medicare_navigator.storage.repository import (
    BasicDrugsFormularyRepository,
    BeneficiaryCostRepository,
    PlanRepository,
    PricingRepository,
)
from medicare_navigator.tools.days_supply import map_pricing_days_supply_to_code
from medicare_navigator.tools.disclaimers import (
    BUG2_CAVEAT,
    BUG4_CAVEAT,
    BUG6_MESSAGE,
    INSULIN_OUT_OF_SCOPE_MESSAGE,
    NO_COST_SHARE_DATA_MESSAGE,
    bug5_caveat,
    bug5b_message,
    pa_st_caveat,
    unmapped_days_supply_caveat,
)
from medicare_navigator.tools.insulin import is_insulin
from medicare_navigator.tools.normalize_drug import compute_benefit_phase, normalize_drug

SOURCE_ID_FALLBACK = "cms_spuf_2026_q1"

# Bug 3: absent per-drug dosing data, assume 1 dose unit ("pill") per day.
DAYS_PER_DOSE_UNIT_DEFAULT = 1


def _source_id() -> str:
    return get_source_id("spuf", SOURCE_ID_FALLBACK)


def _manifest_as_of() -> str:
    return get_as_of("spuf", "2026-01-15")


async def estimate_drug_cost(
    *,
    plan_key: str,
    drug_name: str,
    dosage: str | None = None,
    days_supply: int = 30,
    ytd_oop_spend: float = 0.0,
    pharmacy_channel: str = "preferred_retail",
) -> ToolResult[DrugCostEstimate]:
    as_of = _manifest_as_of()
    source_id = _source_id()

    # Step 1: resolve plan
    plan = PlanRepository().get_plan(plan_key)
    if not plan:
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=source_id,
            as_of_date=as_of,
            message=f"Plan '{plan_key}' not found.",
        )
    if plan["plan_suppressed"]:
        return ToolResult.failure(
            ToolStatus.suppressed,
            source_id=source_id,
            as_of_date=as_of,
            message=BUG6_MESSAGE,
        )

    # Step 2: resolve drug (insulin hard-stop is inline — never a separate, skippable tool call)
    norm = await normalize_drug(drug_name, dosage)
    if norm.status != ToolStatus.ok or not norm.data:
        return ToolResult.failure(
            norm.status,
            source_id=norm.source_id,
            as_of_date=norm.as_of_date,
            message=norm.message,
            data=norm.data,
        )
    selected = norm.data.get("selected") or {}
    resolved_drug_name = selected.get("drug_name", drug_name)
    rxcui = selected.get("rxcui")
    ingredient = selected.get("ingredient")

    if is_insulin(resolved_drug_name, ingredient):
        return ToolResult.failure(
            ToolStatus.insulin_out_of_scope,
            source_id=source_id,
            as_of_date=as_of,
            message=INSULIN_OUT_OF_SCOPE_MESSAGE,
        )

    if not rxcui:
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=source_id,
            as_of_date=as_of,
            message=f"Could not resolve an RxCUI for '{drug_name}'.",
        )

    # Step 3: formulary lookup (+ Bug 5b quantity-limit screening)
    formulary_id = plan.get("formulary_id")
    matches = BasicDrugsFormularyRepository().get_matches(formulary_id, rxcui) if formulary_id else []
    if not matches:
        return ToolResult.failure(
            ToolStatus.not_covered,
            source_id=source_id,
            as_of_date=as_of,
            message=f"'{resolved_drug_name}' is not on plan {plan_key}'s formulary.",
        )

    fill_quantity = ceil(days_supply / DAYS_PER_DOSE_UNIT_DEFAULT)

    surviving = []
    max_allowed_days_supply: int | None = None
    for m in matches:
        blocked = False
        if m.quantity_limit_yn:
            if m.quantity_limit_days is not None and days_supply > m.quantity_limit_days:
                blocked = True
                if max_allowed_days_supply is None or m.quantity_limit_days > max_allowed_days_supply:
                    max_allowed_days_supply = m.quantity_limit_days
            if m.quantity_limit_amount is not None and fill_quantity > m.quantity_limit_amount:
                blocked = True
                candidate_days = int(m.quantity_limit_amount * DAYS_PER_DOSE_UNIT_DEFAULT)
                if max_allowed_days_supply is None or candidate_days > max_allowed_days_supply:
                    max_allowed_days_supply = candidate_days
        if blocked:
            continue
        surviving.append(m)

    any_blocked = len(surviving) < len(matches)

    if not surviving:
        return ToolResult.failure(
            ToolStatus.quantity_limit_blocked,
            source_id=source_id,
            as_of_date=as_of,
            message=bug5b_message(
                requested_days_supply=days_supply,
                max_allowed_days_supply=max_allowed_days_supply or days_supply,
            ),
            data=DrugCostEstimate(
                plan_key=plan_key,
                plan_name=plan["plan_name"],
                drug_name=resolved_drug_name,
                rxcui=rxcui,
                days_supply=days_supply,
                quantity_limit_blocked=True,
                max_allowed_days_supply=max_allowed_days_supply,
                covered=True,
            ),
        )

    pa_flag = any(m.prior_authorization_yn for m in surviving)
    st_flag = any(m.step_therapy_yn for m in surviving)

    # Step 4: days-supply mapping (single named lookup; None = Section 4's explicit "other" branch)
    days_supply_code = map_pricing_days_supply_to_code(days_supply)

    # Step 6: phase determination
    raw_phase = compute_benefit_phase(ytd_oop_spend, float(plan["deductible"]))

    beneficiary_repo = BeneficiaryCostRepository()
    pricing_repo = PricingRepository()

    tiers_matched: list[int] = []
    ndc_costs: list[float] = []
    any_coinsurance_excluded = False

    for m in surviving:
        # Step 6 (cont.): Bug 2 per-tier deductible-exemption override
        ded_applies = beneficiary_repo.get_ded_applies(plan_key, m.tier)
        phase_for_lookup = raw_phase
        if raw_phase == "pre_deductible" and ded_applies is False:
            phase_for_lookup = "initial_coverage"
        # Real CMS SPUF data uses COVERAGE_LEVEL 0=deductible, 1=initial coverage, 3=catastrophic
        # (code 2/coverage-gap is unused post-2025 IRA redesign; confirmed against real ingested
        # data, where code 3 is ~$0 copay/coinsurance in 99%+ of rows). v1 only ever looks up 0/1.
        coverage_level = 0 if phase_for_lookup == "pre_deductible" else 1
        tiers_matched.append(m.tier)

        if phase_for_lookup == "pre_deductible":
            # Beneficiary pays the full (unsubsidized) drug cost pre-deductible.
            # Step 5: pricing lookup (only needed for this branch — copay/coinsurance
            # amounts below come from beneficiary_cost, not the pricing file).
            unit_cost = pricing_repo.get_unit_cost(plan_key, m.ndc, days_supply)
            if unit_cost is None:
                continue
            drug_cost = unit_cost * fill_quantity
            ndc_costs.append(round(drug_cost, 2))
            continue

        # Step 7: cost-share lookup
        cost_share = beneficiary_repo.get_cost_share(
            plan_key,
            m.tier,
            coverage_level=coverage_level,
            days_supply_code=days_supply_code,
            pharmacy_channel=pharmacy_channel,
        )
        if cost_share is None:
            continue
        cost_type, copay, _coinsurance_pct = cost_share
        if cost_type == "coinsurance":
            # COINSURANCE NOT CALCULATED — CONTACT INSURER.
            # CMS record layout does not confirm the dollar base the published coinsurance
            # percentage applies to; computing a figure here would present an unverified
            # number as a firm estimate (Bug 4).
            any_coinsurance_excluded = True
            continue
        ndc_costs.append(round(copay if copay is not None else 0.0, 2))

    matched_ndc_count = len(surviving)
    same_tier = len(set(tiers_matched)) <= 1
    has_cost = bool(ndc_costs)

    caveats: list[str] = [BUG2_CAVEAT]
    if any_coinsurance_excluded:
        caveats.append(BUG4_CAVEAT)
    if days_supply_code is None:
        # "other" branch (Section 4): the raw days-supply doesn't map to a beneficiary_cost
        # CODE at all. Wording differs depending on whether the pre-deductible pricing lookup
        # (keyed on raw days_supply, not the CODE) still produced a number.
        caveats.append(unmapped_days_supply_caveat(days_supply=days_supply, has_cost=has_cost))
    elif not has_cost and not any_coinsurance_excluded:
        # days_supply mapped to a valid CODE, but no beneficiary_cost/pricing row matched this
        # plan/tier/phase/channel — a CMS data gap, not a coinsurance exclusion (Bug 4) or an
        # unmapped days-supply (above). Must not be a silent "ok" with blank numbers.
        caveats.append(NO_COST_SHARE_DATA_MESSAGE)
    if matched_ndc_count > 1 and has_cost:
        caveats.append(
            bug5_caveat(matched_ndc_count=matched_ndc_count, same_tier=same_tier, tiers=tiers_matched)
        )
    if any_blocked:
        caveats.append(
            bug5b_message(
                requested_days_supply=days_supply,
                max_allowed_days_supply=max_allowed_days_supply or days_supply,
            )
        )
    if pa_flag or st_flag:
        caveats.append(pa_st_caveat(prior_authorization=pa_flag, step_therapy=st_flag))

    cost_low = min(ndc_costs) if ndc_costs else None
    cost_high = max(ndc_costs) if ndc_costs else None

    return ToolResult.ok(
        DrugCostEstimate(
            plan_key=plan_key,
            plan_name=plan["plan_name"],
            drug_name=resolved_drug_name,
            rxcui=rxcui,
            tiers_matched=sorted(set(tiers_matched)),
            matched_ndc_count=matched_ndc_count,
            same_tier=same_tier,
            days_supply=days_supply,
            benefit_phase=raw_phase,
            cost_low=cost_low,
            cost_high=cost_high,
            caveats=caveats,
            covered=True,
        ),
        source_id=source_id,
        as_of_date=as_of,
    )
