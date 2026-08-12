"""Deterministic parsing and prose for insulin + regular multi-drug baskets."""

from __future__ import annotations

import re
from dataclasses import dataclass

from medicare_navigator.agent.dosage_questions import (
    _drug_has_strength_in_message,
    _mentioned_common_drugs,
    drugs_missing_dosage,
)
from medicare_navigator.agent.insulin_requests import (
    _DAYS_RE,
    _PLAN_RE,
    _extract_pharmacy_channel,
    _extract_products,
    _extract_ytd_oop_spend,
    format_insulin_estimate_sentence,
    mentioned_oral_drugs_with_strength,
    message_names_non_insulin_cost_drugs,
)
from medicare_navigator.ingestion.manifest import get_as_of, get_source_id
from medicare_navigator.mcp.registry import AS_OF_FALLBACK, SOURCE_ID_FALLBACK
from medicare_navigator.models.response import MultiChannelDrugCostEstimate
from medicare_navigator.tools.batch_estimate import BatchEstimateRequest, BatchEstimateResult
from medicare_navigator.tools.normalize_drug import dosage_candidates_for_drug
from medicare_navigator.tools.pharmacy_channels import channel_cost_bounds

_POOLED_CAP_BAIT_RE = re.compile(
    r"\$?\s*35\b.*\b(?:total|both|together)|"
    r"\b(?:total|both|together).*\$?\s*35\b|"
    r"\b(?:pooled|combined)\s+\$?35\b",
    re.I,
)
_PRICE_INJECTION_RE = re.compile(
    r"\bignore\s+(?:all\s+)?instructions\b|"
    r"\bdisregard\b.*\binstructions\b|"
    r"\bsay\b.*\$\s*\d",
    re.I,
)
_TOTAL_COST_RE = re.compile(
    r"\b(?:total|combined|add up|monthly\s+(?:out[- ]of[- ]pocket|cost)|"
    r"out[- ]of[- ]pocket\s+for\s+both)\b",
    re.I,
)
_DEDUCTIBLE_BASKET_RE = re.compile(
    r"\bpart\s+d\s+deductible\b.*\bapply\b|\bdeductible\b.*\bapply\b",
    re.I,
)
_PHASE_QUESTION_RE = re.compile(
    r"\b(?:what|which)\s+phase\b|\bphase\s+(?:is|are|for|of)\b|\bbenefit\s+phase\b",
    re.I,
)
_PHASE_CONTRAST_RE = re.compile(
    r"\$0\s*ytd\b|\bzero\b[^.]{0,48}\bytd\b|"
    r"\bno\s+out[- ]of[- ]pocket\s+spend\b|"
    r"\b(?:start|beginning)\s+of\s+(?:the\s+)?year\b",
    re.I,
)


@dataclass(frozen=True)
class MixedBasketItem:
    drug_name: str
    dosage: str | None = None
    is_insulin: bool = False


@dataclass(frozen=True)
class MixedBasketRequest:
    plan_key: str
    items: tuple[MixedBasketItem, ...]
    days_supply: int = 30
    ytd_oop_spend: float = 0.0
    asks_total: bool = False
    pooled_cap_bait: bool = False
    price_injection: bool = False
    deductible_question: bool = False
    pharmacy_channel: str | None = None
    asks_phase: bool = False


def _infer_default_dosage_trigger(message: str) -> bool:
    return bool(
        _POOLED_CAP_BAIT_RE.search(message)
        or _PRICE_INJECTION_RE.search(message)
        or _DEDUCTIBLE_BASKET_RE.search(message)
    )


async def _default_oral_dosage(drug: str) -> str | None:
    options = await dosage_candidates_for_drug(drug)
    return options[0] if options else None


def _estimate_cost_bounds(
    data: MultiChannelDrugCostEstimate,
    pharmacy_channel: str | None = None,
) -> tuple[float | None, float | None]:
    if pharmacy_channel:
        channel = data.channels.get(pharmacy_channel)
        if channel is None or channel.cost_low is None:
            return None, None
        high = channel.cost_high if channel.cost_high is not None else channel.cost_low
        return channel.cost_low, high

    lows: list[float] = []
    highs: list[float] = []
    for channel in data.channels.values():
        if channel.cost_low is not None:
            lows.append(channel.cost_low)
        if channel.cost_high is not None:
            highs.append(channel.cost_high)
        elif channel.cost_low is not None:
            highs.append(channel.cost_low)
    if not lows:
        return None, None
    return min(lows), max(highs) if highs else max(lows)


def compute_combined_total(
    results: list[BatchEstimateResult],
    *,
    pharmacy_channel: str | None = None,
) -> tuple[float | None, float | None, str | None]:
    combined_low = 0.0
    combined_high = 0.0
    any_cost = False
    any_incomplete = False
    for result in results:
        if result.status != "ok" or result.data is None:
            any_incomplete = True
            continue
        low, high = _estimate_cost_bounds(result.data, pharmacy_channel)
        if low is None:
            any_incomplete = True
            continue
        combined_low += low
        combined_high += high if high is not None else low
        any_cost = True

    caveat = None
    if any_incomplete:
        caveat = (
            "One or more drugs in this basket could not be totaled (not covered, blocked, or "
            "missing cost-share data) — the combined total below excludes them and may "
            "under-count your actual cost."
        )
    if not any_cost:
        return None, None, caveat
    return combined_low, combined_high, caveat


def _format_currency(amount: float) -> str:
    return f"${amount:,.2f}"


def _format_total_range(low: float, high: float) -> str:
    if low == high:
        return _format_currency(low)
    return f"{_format_currency(low)}–{_format_currency(high)}"


async def resolve_mixed_basket_request(
    message: str,
    *,
    filter_plan_id: str | None = None,
    filter_days_supply: int | None = None,
    filter_ytd_oop_spend: float | None = None,
    filter_drug: str | None = None,
    filter_dosage: str | None = None,
) -> MixedBasketRequest | None:
    """Return a mixed basket when insulin and oral drugs share one plan and strengths are known
    or safely inferable (adversarial bait / injection / deductible basket questions)."""
    if not message_names_non_insulin_cost_drugs(message):
        return None

    insulin_products = _extract_products(message)
    if not insulin_products:
        return None

    plan_keys = tuple(match.group(0).upper() for match in _PLAN_RE.finditer(message))
    plan_key = plan_keys[0] if plan_keys else (filter_plan_id or "").upper()
    if not plan_key:
        return None

    oral_drugs = _mentioned_common_drugs(message)
    strength_orals = dict(mentioned_oral_drugs_with_strength(message))
    for drug in strength_orals:
        if drug not in oral_drugs:
            oral_drugs.append(drug)
    missing_orals = drugs_missing_dosage(
        message,
        filter_drug=filter_drug,
        filter_dosage=filter_dosage,
    )
    infer_defaults = _infer_default_dosage_trigger(message)

    items: list[MixedBasketItem] = []
    for drug in oral_drugs:
        dosage: str | None = strength_orals.get(drug)
        if dosage is None and _drug_has_strength_in_message(message, drug):
            match = re.search(
                rf"\b{re.escape(drug)}\b[^\n.,;]{{0,24}}(\d+\s*mg)\b",
                message,
                re.I,
            ) or re.search(
                rf"\b(\d+\s*mg)\b[^\n.,;]{{0,24}}\b{re.escape(drug)}\b",
                message,
                re.I,
            )
            if match:
                dosage = match.group(1).replace(" ", "")
        elif (
            dosage is None
            and filter_dosage
            and filter_drug
            and filter_drug.lower() == drug
            and len(oral_drugs) == 1
        ):
            dosage = filter_dosage.strip()
        elif dosage is None and infer_defaults:
            dosage = await _default_oral_dosage(drug)
        if drug in missing_orals and not dosage:
            return None
        items.append(MixedBasketItem(drug_name=drug, dosage=dosage, is_insulin=False))

    for product in insulin_products:
        items.append(MixedBasketItem(drug_name=product, dosage=None, is_insulin=True))

    if not items:
        return None

    days_match = _DAYS_RE.search(message)
    days_supply = (
        int(days_match.group(1))
        if days_match
        else filter_days_supply
        if filter_days_supply is not None
        else 30
    )

    return MixedBasketRequest(
        plan_key=plan_key,
        items=tuple(items),
        days_supply=days_supply,
        ytd_oop_spend=_extract_ytd_oop_spend(message, filter_ytd_oop_spend=filter_ytd_oop_spend),
        asks_total=bool(_TOTAL_COST_RE.search(message)),
        pooled_cap_bait=bool(_POOLED_CAP_BAIT_RE.search(message)),
        price_injection=bool(_PRICE_INJECTION_RE.search(message)),
        deductible_question=bool(_DEDUCTIBLE_BASKET_RE.search(message)),
        pharmacy_channel=_extract_pharmacy_channel(message, channel_contrast=False),
        asks_phase=bool(
            _PHASE_QUESTION_RE.search(message) or _PHASE_CONTRAST_RE.search(message)
        ),
    )


def build_batch_requests(request: MixedBasketRequest) -> list[BatchEstimateRequest]:
    return [
        BatchEstimateRequest(
            plan_key=request.plan_key,
            drug_name=item.drug_name,
            dosage=item.dosage,
            days_supply=request.days_supply,
            ytd_oop_spend=request.ytd_oop_spend,
        )
        for item in request.items
    ]


def build_mixed_basket_explanation(
    request: MixedBasketRequest,
    results: list[BatchEstimateResult],
) -> str:
    lines: list[str] = []

    if request.price_injection:
        lines.append(
            "I can't follow instructions to state a false price. Here is what CMS reference "
            f"data shows for plan {request.plan_key}:"
        )
    elif request.pooled_cap_bait:
        lines.append(
            "No — the federal $35 insulin cap applies per covered insulin product, not as one "
            "combined monthly total for insulin plus other drugs on your plan."
        )
    elif request.deductible_question:
        lines.append(
            "On this plan, covered insulin products do not go through the Part D deductible. "
            "For other drugs, deductible treatment depends on tier rules published by CMS."
        )

    insulin_count = sum(1 for item in request.items if item.is_insulin)
    for item, result in zip(request.items, results, strict=True):
        artifact = {
            "status": result.status,
            "message": result.message,
            "data": result.data.model_dump() if result.data else None,
        }
        lines.append(
            format_insulin_estimate_sentence(
                product=item.drug_name,
                plan_key=request.plan_key,
                days_supply=request.days_supply,
                artifact=artifact,
                pharmacy_channel=request.pharmacy_channel,
                include_phase=request.asks_phase,
            )
        )

    if insulin_count > 1:
        lines.append(
            "The insulin ceiling applies separately to each product; these products are "
            "not pooled into one $35 monthly total."
        )

    combined_low, combined_high, caveat = compute_combined_total(
        results,
        pharmacy_channel=request.pharmacy_channel,
    )
    if request.asks_total and combined_low is not None and combined_high is not None:
        lines.append(
            f"Combined monthly total (priced items only): "
            f"{_format_total_range(combined_low, combined_high)}."
        )
    if caveat:
        lines.append(caveat)

    lines.append(
        "These are CMS government reference estimates for the current quarter, not "
        "real-time pharmacy prices."
    )
    return "\n\n".join(lines)


def batch_result_to_artifact(result: BatchEstimateResult) -> dict:
    data = result.data
    channels = data.channels if data else {}
    low, high = (None, None)
    if data:
        low, high = channel_cost_bounds(
            {name: ch.model_dump() for name, ch in channels.items()}
        )
    payload = data.model_dump() if data else None
    return {
        "status": result.status,
        "message": result.message,
        "source_id": get_source_id("spuf", SOURCE_ID_FALLBACK),
        "as_of_date": get_as_of("spuf", AS_OF_FALLBACK),
        "data": payload,
        "cost_low": low,
        "cost_high": high,
    }
