from __future__ import annotations

from medicare_navigator.models.response import CostShareInfo, SupplyEstimate, SupplyScenario
from medicare_navigator.storage.repository import CostTrendRepository, DrugRepository


def _latest_unit_price(ndc: str, plan_key: str | None = None) -> tuple[float | None, list[str]]:
    if plan_key:
        from medicare_navigator.storage.repository import PricingRepository

        unit_cost = PricingRepository().get_unit_cost(plan_key, ndc)
        if unit_cost is not None:
            return unit_cost, ["Unit price from CMS SPUF pricing file"]

    repo = DrugRepository()
    row = repo.db.fetchone(
        "SELECT drug_name, rxcui, ndc, dosage, ingredient FROM drugs WHERE ndc = ?",
        [ndc],
    )
    if not row:
        return None, ["No unit price available for this NDC"]

    trend_repo = CostTrendRepository()
    trends = trend_repo.get_trend(row[1])
    if not trends:
        return None, ["No program spending unit cost available; using copay-only estimate"]

    latest = max(trends, key=lambda t: t.year)
    if latest.avg_unit_cost is None:
        return None, ["No average unit cost in spending trend data"]
    return float(latest.avg_unit_cost), [f"Unit price from program spending data ({latest.year})"]


def compute_supply_estimate(
    *,
    ndc: str,
    plan_key: str | None = None,
    phase: str,
    cost_share: CostShareInfo | None,
    ytd_oop_spend: float,
    quantity: int | None = None,
    fills: int | None = None,
    days_supply: int | None = 30,
    catastrophic_copay: float = 0.0,
) -> SupplyEstimate | None:
    if quantity is None and fills is None:
        return None

    assumptions = [f"Benefit phase: {phase} based on ${ytd_oop_spend:.2f} YTD spend"]
    if days_supply:
        assumptions.append(f"Days supply per fill assumed: {days_supply}")

    use_fills = fills if fills is not None else 1
    cost_type = (cost_share.cost_type if cost_share else "copay") or "copay"

    if phase == "catastrophic":
        total = catastrophic_copay * use_fills
        return SupplyEstimate(
            estimated_patient_cost=total,
            calculation_method="catastrophic",
            formula_description=f"${catastrophic_copay:.2f} catastrophic copay × {use_fills} fill(s)",
            fills=use_fills,
            quantity=quantity,
            assumptions=assumptions,
        )

    unit_price, unit_assumptions = _latest_unit_price(ndc, plan_key=plan_key)

    if quantity is not None and fills is None and quantity >= 2 and cost_type == "copay":
        copay = cost_share.copay if cost_share and cost_share.copay is not None else 0.0
        scenarios = [
            SupplyScenario(
                label=f"1 fill ({quantity} tablets)",
                estimated_patient_cost=copay,
                formula_description=f"${copay:.2f} copay × 1 fill",
                fills=1,
                quantity=quantity,
            ),
            SupplyScenario(
                label=f"{quantity} fills",
                estimated_patient_cost=copay * quantity,
                formula_description=f"${copay:.2f} copay × {quantity} fills",
                fills=quantity,
                quantity=quantity,
            ),
        ]
        return SupplyEstimate(
            estimated_patient_cost=None,
            calculation_method="copay_per_fill",
            formula_description="Ambiguous quantity — see scenarios",
            quantity=quantity,
            assumptions=assumptions + ["Quantity ambiguous — both interpretations shown"],
            scenarios=scenarios,
        )

    if phase == "deductible":
        if unit_price is None:
            return None
        qty = quantity or (days_supply or 30)
        total = unit_price * qty * use_fills
        return SupplyEstimate(
            estimated_patient_cost=round(total, 2),
            calculation_method="deductible_full_price",
            formula_description=(
                f"${unit_price:.2f} unit price × {qty} units × {use_fills} fill(s)"
            ),
            fills=use_fills,
            quantity=qty,
            assumptions=assumptions + unit_assumptions,
        )

    if cost_type == "coinsurance" and cost_share and cost_share.coinsurance_pct is not None:
        if unit_price is None:
            return None
        qty = quantity or (days_supply or 30)
        pct = cost_share.coinsurance_pct / 100.0
        total = unit_price * qty * pct * use_fills
        return SupplyEstimate(
            estimated_patient_cost=round(total, 2),
            calculation_method="coinsurance",
            formula_description=(
                f"${unit_price:.2f} unit price × {qty} units × "
                f"{cost_share.coinsurance_pct:.0f}% coinsurance × {use_fills} fill(s)"
            ),
            fills=use_fills,
            quantity=qty,
            assumptions=assumptions + unit_assumptions,
        )

    copay = cost_share.copay if cost_share and cost_share.copay is not None else 0.0
    total = copay * use_fills
    fill_assumption = (
        ["Interpreted quantity as fill count"]
        if fills
        else ["Interpreted as one fill"]
    )
    return SupplyEstimate(
        estimated_patient_cost=round(total, 2),
        calculation_method="copay_per_fill",
        formula_description=f"${copay:.2f} copay × {use_fills} fill(s)",
        fills=use_fills,
        quantity=quantity,
        assumptions=assumptions + fill_assumption,
    )
