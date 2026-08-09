"""Implements docs/navigator-implementation-spec.md Section 3's 8-step pipeline as one
deterministic function. Consolidated (rather than several LLM-chained tool calls) so the
hard-stop and ordering requirements (suppressed-plan check first; days-supply mapping before
any pricing/beneficiary_cost join; insulin routing) can never be skipped or misordered by an
LLM's tool-call sequencing."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from medicare_navigator.ingestion.manifest import get_as_of, get_source_id
from medicare_navigator.models.response import (
    ChannelCost,
    DrugCostEstimate,
    MultiChannelDrugCostEstimate,
)
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
    CATASTROPHIC_PHASE_NOTE,
    INSULIN_OUT_OF_SCOPE_MESSAGE,
    NO_COST_SHARE_DATA_MESSAGE,
    bug5_caveat,
    bug5b_message,
    pa_st_caveat,
    unmapped_days_supply_caveat,
)
from medicare_navigator.tools.insulin import is_insulin
from medicare_navigator.tools.normalize_drug import compute_benefit_phase, normalize_drug
from medicare_navigator.tools.part_d_benefit_params import (
    cap_fill_copay,
    project_annual_budget,
    project_remaining_year_budget,
)
from medicare_navigator.tools.pharmacy_channels import PHARMACY_CHANNELS

SOURCE_ID_FALLBACK = "cms_spuf_2026_q1"

# Bug 3: absent per-drug dosing data, assume 1 dose unit ("pill") per day.
DAYS_PER_DOSE_UNIT_DEFAULT = 1


def _source_id() -> str:
    return get_source_id("spuf", SOURCE_ID_FALLBACK)


def _manifest_as_of() -> str:
    return get_as_of("spuf", "2026-01-15")


@dataclass
class _EstimateContext:
    plan: dict
    plan_key: str
    resolved_drug_name: str
    dosage: str | None
    rxcui: str
    surviving: list
    days_supply: int
    fill_quantity: int
    days_supply_code: int | None
    raw_phase: str
    ytd_oop_spend: float
    any_blocked: bool
    max_allowed_days_supply: int | None
    pa_flag: bool
    st_flag: bool
    as_of: str
    source_id: str
    quantity_limit_blocked: bool = False


@dataclass
class _ChannelComputation:
    ndc_costs: list[float]
    tiers_matched: list[int]
    any_coinsurance_excluded: bool
    plan_copay: float | None = None
    plan_coinsurance_pct: float | None = None
    applied_copay: float | None = None
    applied_coinsurance_pct: float | None = None


def _coverage_level_for_phase(phase: str) -> int:
    if phase == "pre_deductible":
        return 0
    if phase == "catastrophic":
        return 3
    return 1


def _phase_for_tier_lookup(raw_phase: str, ded_applies: bool | None) -> str:
    if raw_phase == "catastrophic":
        return "catastrophic"
    if raw_phase == "pre_deductible" and ded_applies is False:
        return "initial_coverage"
    return raw_phase


def _ded_applies_label(ded_applies: bool | None) -> str:
    if ded_applies is None:
        return "NA"
    return "Y" if ded_applies else "N"


def _cost_share_copay_and_pct(
    cost_share: tuple[str, float | None, float | None, float | None] | None,
) -> tuple[float | None, float | None]:
    if cost_share is None:
        return None, None
    cost_type, copay, coinsurance_pct, _cost_max = cost_share
    if cost_type == "coinsurance":
        return None, coinsurance_pct
    return copay, None


def _unique_or_none(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    unique = set(present)
    if len(unique) == 1:
        return present[0]
    return None


def _resolve_tier_metadata(
    plan_key: str,
    tiers_matched: list[int],
    raw_phase: str,
    beneficiary_repo: BeneficiaryCostRepository,
) -> tuple[int | None, str, str, list[int]]:
    unique_tiers = sorted(set(tiers_matched))
    tier: int | None = unique_tiers[0] if len(unique_tiers) == 1 else None
    effective_phase = raw_phase
    ded_applies_yn = "NA"

    if len(unique_tiers) == 1:
        ded_applies = beneficiary_repo.get_ded_applies(plan_key, unique_tiers[0])
        ded_applies_yn = _ded_applies_label(ded_applies)
        if raw_phase == "pre_deductible" and ded_applies is False:
            effective_phase = "initial_coverage"
    elif unique_tiers:
        ded_values = {beneficiary_repo.get_ded_applies(plan_key, t) for t in unique_tiers}
        ded_values.discard(None)
        if len(ded_values) == 1:
            ded_applies = ded_values.pop()
            ded_applies_yn = _ded_applies_label(ded_applies)
            if raw_phase == "pre_deductible" and ded_applies is False:
                effective_phase = "initial_coverage"

    return tier, ded_applies_yn, effective_phase, unique_tiers


def _compute_channel_costs(
    ctx: _EstimateContext,
    pharmacy_channel: str,
    beneficiary_repo: BeneficiaryCostRepository,
    pricing_repo: PricingRepository,
) -> _ChannelComputation:
    tiers_matched: list[int] = []
    ndc_costs: list[float] = []
    any_coinsurance_excluded = False
    plan_copays: list[float | None] = []
    plan_pcts: list[float | None] = []
    applied_copays: list[float | None] = []
    applied_pcts: list[float | None] = []

    contract_year = int(ctx.plan.get("contract_year") or 2026)

    for m in ctx.surviving:
        ded_applies = beneficiary_repo.get_ded_applies(ctx.plan_key, m.tier)
        phase_for_lookup = _phase_for_tier_lookup(ctx.raw_phase, ded_applies)
        coverage_level = _coverage_level_for_phase(phase_for_lookup)
        tiers_matched.append(m.tier)

        plan_share = beneficiary_repo.get_cost_share(
            ctx.plan_key,
            m.tier,
            coverage_level=1,
            days_supply_code=ctx.days_supply_code,
            pharmacy_channel=pharmacy_channel,
        )
        plan_copay, plan_pct = _cost_share_copay_and_pct(plan_share)
        plan_copays.append(plan_copay)
        plan_pcts.append(plan_pct)

        if phase_for_lookup == "pre_deductible":
            applied_copays.append(None)
            applied_pcts.append(None)
            unit_cost = pricing_repo.get_unit_cost(ctx.plan_key, m.ndc, ctx.days_supply)
            if unit_cost is None:
                continue
            drug_cost = unit_cost * ctx.fill_quantity
            ndc_costs.append(round(drug_cost, 2))
            continue

        cost_share = beneficiary_repo.get_cost_share(
            ctx.plan_key,
            m.tier,
            coverage_level=coverage_level,
            days_supply_code=ctx.days_supply_code,
            pharmacy_channel=pharmacy_channel,
        )
        if cost_share is None:
            applied_copays.append(None)
            applied_pcts.append(None)
            continue
        cost_type, copay, coinsurance_pct, tier_cost_max = cost_share
        if cost_type == "coinsurance":
            any_coinsurance_excluded = True
            applied_copays.append(None)
            applied_pcts.append(coinsurance_pct)
            continue
        fill_copay = copay if copay is not None else 0.0
        if phase_for_lookup != "catastrophic":
            fill_copay = cap_fill_copay(fill_copay, tier_cost_max, contract_year)
        applied_copays.append(round(fill_copay, 2))
        applied_pcts.append(None)
        ndc_costs.append(round(fill_copay, 2))

    return _ChannelComputation(
        ndc_costs=ndc_costs,
        tiers_matched=tiers_matched,
        any_coinsurance_excluded=any_coinsurance_excluded,
        plan_copay=_unique_or_none(plan_copays),
        plan_coinsurance_pct=_unique_or_none(plan_pcts),
        applied_copay=_unique_or_none(applied_copays),
        applied_coinsurance_pct=_unique_or_none(applied_pcts),
    )


def _build_caveats(
    *,
    ctx: _EstimateContext,
    matched_ndc_count: int,
    same_tier: bool,
    tiers_matched: list[int],
    has_cost: bool,
    any_coinsurance_excluded: bool,
    include_no_cost_share: bool,
) -> list[str]:
    caveats: list[str] = [BUG2_CAVEAT]
    if ctx.raw_phase == "catastrophic":
        caveats.append(CATASTROPHIC_PHASE_NOTE)
    if any_coinsurance_excluded:
        caveats.append(BUG4_CAVEAT)
    if ctx.days_supply_code is None:
        caveats.append(
            unmapped_days_supply_caveat(days_supply=ctx.days_supply, has_cost=has_cost)
        )
    elif not has_cost and not any_coinsurance_excluded and include_no_cost_share:
        caveats.append(NO_COST_SHARE_DATA_MESSAGE)
    if matched_ndc_count > 1 and has_cost:
        caveats.append(
            bug5_caveat(matched_ndc_count=matched_ndc_count, same_tier=same_tier, tiers=tiers_matched)
        )
    if ctx.any_blocked:
        caveats.append(
            bug5b_message(
                requested_days_supply=ctx.days_supply,
                max_allowed_days_supply=ctx.max_allowed_days_supply or ctx.days_supply,
            )
        )
    if ctx.pa_flag or ctx.st_flag:
        caveats.append(pa_st_caveat(prior_authorization=ctx.pa_flag, step_therapy=ctx.st_flag))
    return caveats


def _empty_channel_costs() -> dict[str, ChannelCost]:
    return {channel: ChannelCost() for channel in PHARMACY_CHANNELS}


async def _resolve_estimate_context(
    *,
    plan_key: str,
    drug_name: str,
    dosage: str | None,
    days_supply: int,
    ytd_oop_spend: float,
) -> ToolResult | _EstimateContext:
    as_of = _manifest_as_of()
    source_id = _source_id()

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
    resolved_dosage = selected.get("dosage") or dosage
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

    formulary_id = plan.get("formulary_id")
    matches = BasicDrugsFormularyRepository().get_matches(formulary_id, rxcui) if formulary_id else []
    if not matches:
        return ToolResult.failure(
            ToolStatus.not_covered,
            source_id=source_id,
            as_of_date=as_of,
            message=f"'{resolved_drug_name}' is not on plan {plan_key}'s formulary.",
            data=MultiChannelDrugCostEstimate(
                plan_key=plan_key,
                plan_name=plan["plan_name"],
                drug_name=resolved_drug_name,
                dosage=resolved_dosage,
                rxcui=rxcui,
                covered=False,
                days_supply=days_supply,
                ytd_oop_spend=ytd_oop_spend,
                deductible=float(plan["deductible"]) if plan.get("deductible") is not None else None,
                channels=_empty_channel_costs(),
            ),
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
            data=MultiChannelDrugCostEstimate(
                plan_key=plan_key,
                plan_name=plan["plan_name"],
                drug_name=resolved_drug_name,
                dosage=resolved_dosage,
                rxcui=rxcui,
                covered=True,
                days_supply=days_supply,
                ytd_oop_spend=ytd_oop_spend,
                deductible=float(plan["deductible"]) if plan.get("deductible") is not None else None,
                channels=_empty_channel_costs(),
                quantity_limit_blocked=True,
                max_allowed_days_supply=max_allowed_days_supply,
            ),
        )

    raw_phase = compute_benefit_phase(
        ytd_oop_spend,
        float(plan["deductible"]),
        contract_year=int(plan.get("contract_year") or 2026),
    )
    days_supply_code = map_pricing_days_supply_to_code(days_supply)

    return _EstimateContext(
        plan=plan,
        plan_key=plan_key,
        resolved_drug_name=resolved_drug_name,
        dosage=resolved_dosage,
        rxcui=rxcui,
        surviving=surviving,
        days_supply=days_supply,
        fill_quantity=fill_quantity,
        days_supply_code=days_supply_code,
        raw_phase=raw_phase,
        ytd_oop_spend=ytd_oop_spend,
        any_blocked=any_blocked,
        max_allowed_days_supply=max_allowed_days_supply,
        pa_flag=any(m.prior_authorization_yn for m in surviving),
        st_flag=any(m.step_therapy_yn for m in surviving),
        as_of=as_of,
        source_id=source_id,
    )


def _channel_cost_bounds(channels: dict[str, ChannelCost]) -> tuple[float | None, float | None]:
    lows: list[float] = []
    highs: list[float] = []
    for channel in channels.values():
        if channel.cost_low is not None:
            lows.append(channel.cost_low)
        if channel.cost_high is not None:
            highs.append(channel.cost_high)
        elif channel.cost_low is not None:
            highs.append(channel.cost_low)
    if not lows:
        return None, None
    return min(lows), max(highs) if highs else max(lows)


def _apply_annual_budget_fields(
    estimate: MultiChannelDrugCostEstimate,
    *,
    plan: dict,
    ytd_oop_spend: float,
    days_supply: int,
) -> None:
    from medicare_navigator.agent.datetime_context import days_remaining_in_contract_year
    from medicare_navigator.agent.request_context import get_request_timezone

    contract_year = int(plan.get("contract_year") or 2026)
    cost_low, cost_high = _channel_cost_bounds(estimate.channels)
    cap, headroom, budget_low, budget_high = project_annual_budget(
        ytd_oop_spend=ytd_oop_spend,
        days_supply=days_supply,
        cost_low=cost_low,
        cost_high=cost_high,
        contract_year=contract_year,
    )
    estimate.annual_oop_cap = cap
    estimate.remaining_oop_headroom = headroom
    estimate.annual_budget_cost_low = budget_low
    estimate.annual_budget_cost_high = budget_high

    days_remaining = days_remaining_in_contract_year(contract_year, get_request_timezone())
    (
        _cap,
        _headroom,
        remaining_low,
        remaining_high,
        remaining_days,
        remaining_fills,
    ) = project_remaining_year_budget(
        ytd_oop_spend=ytd_oop_spend,
        days_supply=days_supply,
        cost_low=cost_low,
        cost_high=cost_high,
        contract_year=contract_year,
        days_remaining=days_remaining,
    )
    estimate.remaining_year_days = remaining_days
    estimate.remaining_year_fills = remaining_fills
    estimate.remaining_year_budget_cost_low = remaining_low
    estimate.remaining_year_budget_cost_high = remaining_high


def _multi_channel_from_context(ctx: _EstimateContext) -> MultiChannelDrugCostEstimate:
    beneficiary_repo = BeneficiaryCostRepository()
    pricing_repo = PricingRepository()

    channels: dict[str, ChannelCost] = {}
    any_coinsurance = False
    any_has_cost = False
    reference_tiers: list[int] = []

    for channel in PHARMACY_CHANNELS:
        computed = _compute_channel_costs(ctx, channel, beneficiary_repo, pricing_repo)
        reference_tiers = computed.tiers_matched
        has_cost = bool(computed.ndc_costs)
        any_has_cost = any_has_cost or has_cost
        any_coinsurance = any_coinsurance or computed.any_coinsurance_excluded
        channels[channel] = ChannelCost(
            cost_low=min(computed.ndc_costs) if computed.ndc_costs else None,
            cost_high=max(computed.ndc_costs) if computed.ndc_costs else None,
            coinsurance=computed.any_coinsurance_excluded,
            plan_copay=computed.plan_copay,
            plan_coinsurance_pct=computed.plan_coinsurance_pct,
            applied_copay=computed.applied_copay,
            applied_coinsurance_pct=computed.applied_coinsurance_pct,
        )

    matched_ndc_count = len(ctx.surviving)
    same_tier = len(set(reference_tiers)) <= 1
    tier, ded_applies_yn, effective_phase, unique_tiers = _resolve_tier_metadata(
        ctx.plan_key, reference_tiers, ctx.raw_phase, beneficiary_repo
    )
    deductible = ctx.plan.get("deductible")
    caveats = _build_caveats(
        ctx=ctx,
        matched_ndc_count=matched_ndc_count,
        same_tier=same_tier,
        tiers_matched=reference_tiers,
        has_cost=any_has_cost,
        any_coinsurance_excluded=any_coinsurance,
        include_no_cost_share=False,
    )

    result = MultiChannelDrugCostEstimate(
        plan_key=ctx.plan_key,
        plan_name=ctx.plan["plan_name"],
        drug_name=ctx.resolved_drug_name,
        dosage=ctx.dosage,
        rxcui=ctx.rxcui,
        covered=True,
        days_supply=ctx.days_supply,
        ytd_oop_spend=ctx.ytd_oop_spend,
        deductible=float(deductible) if deductible is not None else None,
        tier=tier,
        tiers_matched=unique_tiers,
        ded_applies_yn=ded_applies_yn,
        benefit_phase=ctx.raw_phase,
        effective_phase=effective_phase,
        channels=channels,
        matched_ndc_count=matched_ndc_count,
        same_tier=same_tier,
        caveats=caveats,
        quantity_limit_blocked=ctx.quantity_limit_blocked,
        max_allowed_days_supply=ctx.max_allowed_days_supply,
    )
    _apply_annual_budget_fields(
        result,
        plan=ctx.plan,
        ytd_oop_spend=ctx.ytd_oop_spend,
        days_supply=ctx.days_supply,
    )
    return result


async def estimate_drug_cost_all_channels(
    *,
    plan_key: str,
    drug_name: str,
    dosage: str | None = None,
    days_supply: int = 30,
    ytd_oop_spend: float = 0.0,
) -> ToolResult[MultiChannelDrugCostEstimate]:
    resolved = await _resolve_estimate_context(
        plan_key=plan_key,
        drug_name=drug_name,
        dosage=dosage,
        days_supply=days_supply,
        ytd_oop_spend=ytd_oop_spend,
    )
    if isinstance(resolved, ToolResult):
        return resolved

    data = _multi_channel_from_context(resolved)
    return ToolResult.ok(
        data,
        source_id=resolved.source_id,
        as_of_date=resolved.as_of,
    )


async def estimate_drug_cost(
    *,
    plan_key: str,
    drug_name: str,
    dosage: str | None = None,
    days_supply: int = 30,
    ytd_oop_spend: float = 0.0,
    pharmacy_channel: str = "preferred_retail",
) -> ToolResult[DrugCostEstimate]:
    resolved = await _resolve_estimate_context(
        plan_key=plan_key,
        drug_name=drug_name,
        dosage=dosage,
        days_supply=days_supply,
        ytd_oop_spend=ytd_oop_spend,
    )
    if isinstance(resolved, ToolResult):
        if resolved.data and isinstance(resolved.data, MultiChannelDrugCostEstimate):
            partial = resolved.data
            return ToolResult.failure(
                resolved.status,
                source_id=resolved.source_id,
                as_of_date=resolved.as_of_date,
                message=resolved.message,
                data=DrugCostEstimate(
                    plan_key=partial.plan_key,
                    plan_name=partial.plan_name,
                    drug_name=partial.drug_name or drug_name,
                    rxcui=partial.rxcui,
                    days_supply=days_supply,
                    covered=partial.covered if partial.covered is not None else False,
                    quantity_limit_blocked=partial.quantity_limit_blocked,
                    max_allowed_days_supply=partial.max_allowed_days_supply,
                ),
            )
        return resolved

    ctx = resolved
    beneficiary_repo = BeneficiaryCostRepository()
    pricing_repo = PricingRepository()
    computed = _compute_channel_costs(ctx, pharmacy_channel, beneficiary_repo, pricing_repo)

    matched_ndc_count = len(ctx.surviving)
    same_tier = len(set(computed.tiers_matched)) <= 1
    has_cost = bool(computed.ndc_costs)
    caveats = _build_caveats(
        ctx=ctx,
        matched_ndc_count=matched_ndc_count,
        same_tier=same_tier,
        tiers_matched=computed.tiers_matched,
        has_cost=has_cost,
        any_coinsurance_excluded=computed.any_coinsurance_excluded,
        include_no_cost_share=True,
    )

    cost_low = min(computed.ndc_costs) if computed.ndc_costs else None
    cost_high = max(computed.ndc_costs) if computed.ndc_costs else None

    return ToolResult.ok(
        DrugCostEstimate(
            plan_key=ctx.plan_key,
            plan_name=ctx.plan["plan_name"],
            drug_name=ctx.resolved_drug_name,
            rxcui=ctx.rxcui,
            tiers_matched=sorted(set(computed.tiers_matched)),
            matched_ndc_count=matched_ndc_count,
            same_tier=same_tier,
            days_supply=ctx.days_supply,
            benefit_phase=ctx.raw_phase,
            cost_low=cost_low,
            cost_high=cost_high,
            caveats=caveats,
            covered=True,
        ),
        source_id=ctx.source_id,
        as_of_date=ctx.as_of,
    )
