"""Shared concurrent runner for the multi-drug and plan-comparison features. Both loop the
same single-(drug, plan) pipeline (estimate_drug_cost_all_channels); this module runs a list
of such requests with asyncio.gather and isolates a single bad drug/plan from failing the
whole batch."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from medicare_navigator.models.response import MultiChannelDrugCostEstimate
from medicare_navigator.tools.estimate_drug_cost import estimate_drug_cost_all_channels

MAX_BATCH_DRUGS = 5
MAX_COMPARE_PLANS = 4


@dataclass(frozen=True)
class BatchEstimateRequest:
    plan_key: str
    drug_name: str
    dosage: str | None = None
    days_supply: int = 30
    ytd_oop_spend: float = 0.0


@dataclass(frozen=True)
class BatchEstimateResult:
    request: BatchEstimateRequest
    data: MultiChannelDrugCostEstimate | None
    status: str
    message: str | None


async def _run_one(request: BatchEstimateRequest) -> BatchEstimateResult:
    try:
        result = await estimate_drug_cost_all_channels(
            plan_key=request.plan_key,
            drug_name=request.drug_name,
            dosage=request.dosage,
            days_supply=request.days_supply,
            ytd_oop_spend=request.ytd_oop_spend,
        )
    except Exception as exc:  # isolate one bad drug/plan from breaking the whole batch
        return BatchEstimateResult(request=request, data=None, status="error", message=str(exc))

    data = result.data if isinstance(result.data, MultiChannelDrugCostEstimate) else None
    return BatchEstimateResult(
        request=request,
        data=data,
        status=result.status.value,
        message=result.message,
    )


async def run_batch_estimates(
    requests: list[BatchEstimateRequest],
) -> list[BatchEstimateResult]:
    """Run requests concurrently, preserving input order (asyncio.gather already does)."""
    if not requests:
        return []
    return list(await asyncio.gather(*(_run_one(r) for r in requests)))
