from __future__ import annotations

from medicare_navigator.ingestion.manifest import get_as_of, get_contract_year, get_source_id
from medicare_navigator.models.response import CostShareInfo, FormularyResult
from medicare_navigator.models.tool_result import ToolResult, ToolStatus
from medicare_navigator.storage.repository import FormularyRepository, PlanRepository
from medicare_navigator.tools.normalize_drug import compute_benefit_phase, load_benefit_params
from medicare_navigator.tools.supply_estimate import compute_supply_estimate

SOURCE_ID_FALLBACK = "cms_spuf_2026_q1_demo"


def _source_id() -> str:
    return get_source_id("spuf", SOURCE_ID_FALLBACK)


def _manifest_as_of() -> str:
    return get_as_of("spuf", "2026-01-15")


def _check_stale(contract_year: int) -> ToolStatus | None:
    manifest_year = get_contract_year(contract_year)
    if manifest_year != contract_year:
        return ToolStatus.stale
    return None


def formulary_benefit_lookup(
    plan_key: str,
    ndc: str,
    ytd_oop_spend: float = 0.0,
    contract_year: int = 2026,
    ytd_oop_spend_provided: bool = False,
    quantity: int | None = None,
    fills: int | None = None,
    days_supply: int | None = 30,
    pharmacy_channel: str = "preferred_retail",
) -> ToolResult[FormularyResult]:
    as_of = _manifest_as_of()
    source_id = _source_id()
    stale = _check_stale(contract_year)

    plan_repo = PlanRepository()
    plan = plan_repo.get_plan(plan_key)
    if not plan:
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=source_id,
            as_of_date=as_of,
            message=f"Plan '{plan_key}' not found.",
        )

    formulary_repo = FormularyRepository()
    entry = formulary_repo.get_formulary_entry(
        plan_key, ndc, pharmacy_channel=pharmacy_channel
    )
    params = load_benefit_params(contract_year)
    oop_threshold = float(params["oop_threshold"])
    deductible = float(plan["deductible"])
    catastrophic_copay = float(params.get("catastrophic_cost_share", 0))

    if not entry:
        not_covered = FormularyResult(
            plan_key=plan_key,
            plan_name=plan["plan_name"],
            tier=None,
            cost_share=None,
            benefit_phase=None,
            ytd_oop_spend=ytd_oop_spend,
            oop_threshold=oop_threshold,
            deductible=deductible,
            covered=False,
            ytd_oop_spend_assumed=not ytd_oop_spend_provided,
        )
        return ToolResult(
            status=ToolStatus.not_covered,
            data=not_covered,
            source_id=source_id,
            as_of_date=as_of,
            message=f"Drug NDC {ndc} is not covered on plan {plan_key}.",
        )

    phase = compute_benefit_phase(ytd_oop_spend, deductible, oop_threshold)

    cost_share = CostShareInfo(
        tier=entry.tier,
        copay=entry.copay,
        coinsurance_pct=entry.coinsurance_pct,
        cost_type=entry.cost_type,
    )

    supply_estimate = compute_supply_estimate(
        ndc=ndc,
        plan_key=plan_key,
        phase=phase,
        cost_share=cost_share,
        ytd_oop_spend=ytd_oop_spend,
        quantity=quantity,
        fills=fills,
        days_supply=days_supply,
        catastrophic_copay=catastrophic_copay,
    )

    result = FormularyResult(
        plan_key=plan_key,
        plan_name=entry.plan_name,
        tier=entry.tier,
        cost_share=cost_share,
        benefit_phase=phase,
        ytd_oop_spend=ytd_oop_spend,
        oop_threshold=oop_threshold,
        deductible=deductible,
        covered=True,
        ytd_oop_spend_assumed=not ytd_oop_spend_provided,
        supply_estimate=supply_estimate,
    )
    status = stale or ToolStatus.ok
    return ToolResult(
        status=status,
        data=result,
        source_id=source_id,
        as_of_date=as_of,
        message=(
            f"Data is from contract year {get_contract_year(contract_year)}; "
            f"requested year {contract_year}."
            if stale
            else None
        ),
    )
