from __future__ import annotations

import json

from medicare_navigator.config import settings
from medicare_navigator.models.response import CostShareInfo, FormularyResult
from medicare_navigator.models.tool_result import ToolResult, ToolStatus
from medicare_navigator.storage.repository import FormularyRepository, PlanRepository
from medicare_navigator.tools.normalize_drug import compute_benefit_phase, load_benefit_params
from medicare_navigator.tools.supply_estimate import compute_supply_estimate

SOURCE_ID = "cms_spuf_2026_q1_demo"


def _manifest_as_of() -> str:
    manifest_path = settings.data_dir / "manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return data.get("spuf", {}).get("as_of", "2026-01-15")
    return "2026-01-15"


def formulary_benefit_lookup(
    plan_key: str,
    ndc: str,
    ytd_oop_spend: float = 0.0,
    contract_year: int = 2026,
    ytd_oop_spend_provided: bool = False,
    quantity: int | None = None,
    fills: int | None = None,
    days_supply: int | None = 30,
) -> ToolResult[FormularyResult]:
    as_of = _manifest_as_of()
    plan_repo = PlanRepository()
    plan = plan_repo.get_plan(plan_key)
    if not plan:
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message=f"Plan '{plan_key}' not found in demo plan set.",
        )

    formulary_repo = FormularyRepository()
    entry = formulary_repo.get_formulary_entry(plan_key, ndc)
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
            source_id=SOURCE_ID,
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
    return ToolResult.ok(result, source_id=SOURCE_ID, as_of_date=as_of)
