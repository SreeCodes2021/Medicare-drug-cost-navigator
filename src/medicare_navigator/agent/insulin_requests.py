"""Deterministic parsing for insulin cost requests.

The LLM should summarize insulin estimates, not decide which named products
were present in a cost question.  This module intentionally extracts only
well-known insulin product names; a bare "insulin" reference remains a policy
question or a clarification request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from medicare_navigator.agent.dosage_questions import _mentioned_common_drugs
from medicare_navigator.tools.insulin import _INSULIN_NAMES
from medicare_navigator.tools.part_d_benefit_params import annual_oop_cap
from medicare_navigator.tools.pharmacy_channels import (
    PHARMACY_CHANNEL_LABELS,
    channel_cost_bounds,
)


_PRODUCT_ALIASES = tuple(
    sorted(
        (name for name in _INSULIN_NAMES if name != "insulin"),
        key=len,
        reverse=True,
    )
)
_PLAN_RE = re.compile(r"\b[A-Za-z]\d{4}-\d{3}\b")
_DAYS_RE = re.compile(r"\b(\d+)\s*[- ]?day(?:s)?\b", re.I)
_YTD_RE = re.compile(
    r"\b(?:ytd|year[- ]to[- ]date|spent)\D{0,20}\$?\s*(\d[\d,]*(?:\.\d+)?)",
    re.I,
)
_YTD_SUFFIX_RE = re.compile(
    r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(?:ytd|year[- ]to[- ]date)\b",
    re.I,
)
_ZERO_YTD_RE = re.compile(
    r"\b(?:zero|no)\b[^.]{0,48}\b(?:ytd|out[- ]of[- ]pocket|oop)\b|"
    r"\b(?:start|beginning)\s+of\s+(?:the\s+)?year\b",
    re.I,
)
_ORAL_STRENGTH_DRUG_RE = re.compile(
    r"\b([a-z][a-z-]{2,})\s+(\d+(?:\.\d+)?\s*mg)\b",
    re.I,
)
_ORAL_STRENGTH_DRUG_REVERSED_RE = re.compile(
    r"\b(\d+(?:\.\d+)?\s*mg)\b[^\n.,;]{0,24}\b([a-z][a-z-]{2,})\b",
    re.I,
)
_ORAL_DRUG_STOPWORDS = frozenset(
    {
        "and",
        "cost",
        "each",
        "for",
        "plan",
        "supply",
        "total",
        "what",
        "with",
    }
)
_MET_OOP_AMOUNT_RE = re.compile(
    r"\bmet\s+(?:my\s+)?\$?\s*([\d,]+(?:\.\d+)?)\s*(?:annual\s+)?(?:out[- ]of[- ]pocket|oop)\b",
    re.I,
)
_CATASTROPHIC_RE = re.compile(
    r"\b(?:catastrophic(?:\s+coverage)?|met\s+(?:my\s+)?(?:annual\s+)?(?:out[- ]of[- ]pocket|oop)\s+max(?:imum)?)\b",
    re.I,
)
_COMPARE_RE = re.compile(r"\bcompare|versus|vs\.?|between\b", re.I)
_TIER_RE = re.compile(r"\b(?:what|which)\s+(?:formulary\s+)?tier\b|\bformulary\s+tier\b", re.I)
_CHANNEL_CONTRAST_RE = re.compile(
    r"\bmail(?:[- ]order)?\b.*\bretail\b|\bretail\b.*\bmail(?:[- ]order)?\b",
    re.I,
)
_POLICY_CEILING_RE = re.compile(
    r"\b(?:always|exactly|every\s+month|ceiling|maximum|can\s+it\s+be\s+lower|could\s+it\s+be\s+lower|capped\s+at\s+\$?35)\b",
    re.I,
)
_POLICY_DEDUCTIBLE_RE = re.compile(r"\bdeductible\b", re.I)
_POLICY_IRA_RE = re.compile(r"\bwhy\b", re.I)
_GENERIC_POLICY_RE = re.compile(
    r"\b(?:cap|capped|always|maximum|deductible|policy|ceiling)\b",
    re.I,
)

INSULIN_INTENT_COST = "cost"
INSULIN_INTENT_POLICY_CEILING = "policy_ceiling"
INSULIN_INTENT_POLICY_DEDUCTIBLE = "policy_deductible"
INSULIN_INTENT_POLICY_IRA = "policy_ira"
INSULIN_INTENT_POLICY_CATASTROPHIC = "policy_catastrophic"
INSULIN_INTENT_TIER_LOOKUP = "tier_lookup"
INSULIN_INTENT_CHANNEL_CONTRAST = "channel_contrast"
INSULIN_INTENT_MULTI_PLAN_COMPARE = "multi_plan_compare"


@dataclass(frozen=True)
class InsulinRequest:
    products: tuple[str, ...]
    plan_key: str | None
    plan_keys: tuple[str, ...] = ()
    days_supply: int = 30
    ytd_oop_spend: float = 0.0
    pharmacy_channel: str | None = None
    is_policy_question: bool = False
    intent: str = INSULIN_INTENT_COST


def _contains_product(message: str, alias: str) -> bool:
    return bool(re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", message, re.I))


def _extract_products(message: str) -> tuple[str, ...]:
    products: list[str] = []
    for alias in _PRODUCT_ALIASES:
        if _contains_product(message, alias):
            products.append(alias)
    return tuple(sorted(products, key=message.lower().index))


def _extract_plan_keys(message: str) -> tuple[str, ...]:
    return tuple(match.group(0).upper() for match in _PLAN_RE.finditer(message))


def _parse_ytd_amount(raw: str | None) -> float | None:
    if not raw:
        return None
    cleaned = raw.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_ytd_oop_spend(
    message: str,
    *,
    filter_ytd_oop_spend: float | None,
) -> float:
    for pattern in (_YTD_SUFFIX_RE, _YTD_RE):
        match = pattern.search(message)
        if match:
            amount = _parse_ytd_amount(match.group(1))
            if amount is not None:
                return amount
    if _ZERO_YTD_RE.search(message):
        return 0.0
    if filter_ytd_oop_spend is not None:
        return filter_ytd_oop_spend
    met_oop = _MET_OOP_AMOUNT_RE.search(message)
    if met_oop:
        return float(met_oop.group(1).replace(",", ""))
    if _CATASTROPHIC_RE.search(message):
        return annual_oop_cap(2026)
    return 0.0


def _extract_pharmacy_channel(message: str, *, channel_contrast: bool) -> str | None:
    if channel_contrast:
        return None
    lowered = message.lower()
    if "preferred" in lowered and "retail" in lowered:
        return "preferred_retail"
    if "standard" in lowered and "retail" in lowered:
        return "standard_retail"
    if "preferred" in lowered and re.search(r"\bmail(?:[- ]order)?\b", lowered):
        return "preferred_mail"
    if "standard" in lowered and re.search(r"\bmail(?:[- ]order)?\b", lowered):
        return "standard_mail"
    return None


def _resolve_intent(
    message: str,
    *,
    products: tuple[str, ...],
    plan_keys: tuple[str, ...],
    channel_contrast: bool,
) -> str:
    if channel_contrast:
        return INSULIN_INTENT_CHANNEL_CONTRAST
    if _TIER_RE.search(message):
        return INSULIN_INTENT_TIER_LOOKUP
    if len(plan_keys) >= 2 and _COMPARE_RE.search(message):
        return INSULIN_INTENT_MULTI_PLAN_COMPARE
    if _CATASTROPHIC_RE.search(message):
        return INSULIN_INTENT_POLICY_CATASTROPHIC
    if _POLICY_DEDUCTIBLE_RE.search(message):
        return INSULIN_INTENT_POLICY_DEDUCTIBLE
    if _POLICY_IRA_RE.search(message):
        return INSULIN_INTENT_POLICY_IRA
    if products and _POLICY_CEILING_RE.search(message):
        return INSULIN_INTENT_POLICY_CEILING
    return INSULIN_INTENT_COST


def mentioned_oral_drugs_with_strength(message: str) -> list[tuple[str, str]]:
    """Return (drug, dosage) pairs for explicit oral strengths, excluding insulin products."""
    insulin_products = set(_extract_products(message))
    found: dict[str, str] = {}
    for match in _ORAL_STRENGTH_DRUG_RE.finditer(message):
        drug = match.group(1).lower()
        if drug in _ORAL_DRUG_STOPWORDS or drug in insulin_products:
            continue
        found.setdefault(drug, match.group(2).replace(" ", ""))
    for match in _ORAL_STRENGTH_DRUG_REVERSED_RE.finditer(message):
        drug = match.group(2).lower()
        if drug in _ORAL_DRUG_STOPWORDS or drug in insulin_products:
            continue
        found.setdefault(drug, match.group(1).replace(" ", ""))
    return list(found.items())


def message_names_non_insulin_cost_drugs(message: str) -> bool:
    """True when the message names oral/common drugs alongside insulin products."""
    return bool(_mentioned_common_drugs(message) or mentioned_oral_drugs_with_strength(message))


def resolve_insulin_request(
    message: str,
    *,
    filter_plan_id: str | None = None,
    filter_days_supply: int | None = None,
    filter_ytd_oop_spend: float | None = None,
) -> InsulinRequest | None:
    """Return a request only when the message explicitly names insulin."""
    products = _extract_products(message)
    generic = bool(re.search(r"\binsulin\b", message, re.I))
    if not products and not generic:
        return None

    plan_keys = _extract_plan_keys(message)
    if not plan_keys and filter_plan_id:
        plan_keys = (filter_plan_id.upper(),)
    channel_contrast = bool(_CHANNEL_CONTRAST_RE.search(message))
    days_match = _DAYS_RE.search(message)
    intent = _resolve_intent(
        message,
        products=products,
        plan_keys=plan_keys,
        channel_contrast=channel_contrast,
    )

    return InsulinRequest(
        products=products,
        plan_key=plan_keys[0] if plan_keys else filter_plan_id,
        plan_keys=plan_keys,
        days_supply=(
            int(days_match.group(1))
            if days_match
            else filter_days_supply
            if filter_days_supply is not None
            else 30
        ),
        ytd_oop_spend=_extract_ytd_oop_spend(
            message,
            filter_ytd_oop_spend=filter_ytd_oop_spend,
        ),
        pharmacy_channel=_extract_pharmacy_channel(message, channel_contrast=channel_contrast),
        is_policy_question=(
            not products
            and generic
            and bool(_GENERIC_POLICY_RE.search(message))
        ),
        intent=intent,
    )


def insulin_policy_preamble(intent: str) -> str | None:
    if intent == INSULIN_INTENT_POLICY_CEILING:
        return (
            "No — the federal $35 insulin cap is a ceiling, not a fixed monthly price. "
            "When CMS publishes a lower plan copay for a covered product, your share can "
            "be below $35."
        )
    if intent == INSULIN_INTENT_POLICY_DEDUCTIBLE:
        return (
            "No — covered insulin products do not go through the Part D deductible phase. "
            "Your cost-sharing follows the insulin cap path instead."
        )
    if intent == INSULIN_INTENT_POLICY_IRA:
        return (
            "The estimate reflects the Inflation Reduction Act's $35-per-30-day insulin "
            "cap, applied from CMS's insulin-specific cost-share file for your plan and tier."
        )
    if intent == INSULIN_INTENT_POLICY_CATASTROPHIC:
        return (
            "In catastrophic coverage, covered Part D drugs are typically $0 — the $35 "
            "insulin cap applies only before you reach the annual out-of-pocket maximum."
        )
    return None


def _format_currency(amount: float) -> str:
    return f"${amount:,.2f}"


def _channel_amount(channels: dict[str, Any], channel_name: str) -> float | None:
    channel = channels.get(channel_name) or {}
    low = channel.get("cost_low")
    if low is None:
        return None
    return float(low)


def _format_cost_amount(low: float | None, high: float | None) -> str | None:
    if low is None:
        return None
    if high is None or low == high:
        return _format_currency(low)
    return f"{_format_currency(low)}–{_format_currency(high)}"


def _format_phase_label(phase: str | None) -> str | None:
    if not phase:
        return None
    return {
        "pre_deductible": "pre-deductible",
        "initial_coverage": "initial coverage",
        "insulin_cap": "insulin cap",
        "catastrophic": "catastrophic coverage",
    }.get(phase, phase.replace("_", " "))


def _cost_bounds_from_channels(
    channels: dict[str, Any],
    pharmacy_channel: str | None,
) -> tuple[float | None, float | None]:
    if pharmacy_channel:
        channel = channels.get(pharmacy_channel) or {}
        low = channel.get("cost_low")
        if low is None:
            return None, None
        high = channel.get("cost_high")
        return float(low), float(high if high is not None else low)
    return channel_cost_bounds(channels)


def format_insulin_estimate_sentence(
    *,
    product: str,
    plan_key: str,
    days_supply: int,
    artifact: dict[str, Any],
    intent: str = INSULIN_INTENT_COST,
    pharmacy_channel: str | None = None,
    include_phase: bool = False,
) -> str:
    status = artifact.get("status")
    data = artifact.get("data") or {}
    product_title = product.title()

    if status in {"suppressed", "insulin_out_of_scope", "quantity_limit_blocked"}:
        detail = artifact.get("message") or f"No estimate is available for {product_title}."
        if product_title.lower() not in detail.lower():
            return f"{product_title} on plan {plan_key}: {detail}"
        return f"On plan {plan_key}: {detail}"
    if status == "not_covered" or data.get("covered") is False:
        return (
            f"{product_title} is not covered on plan {plan_key}, so no Medicare cost "
            "estimate is available."
        )

    tier = data.get("tier")
    tiers = data.get("tiers_matched") or []
    if tier is None and tiers:
        tier = tiers[0]

    if intent == INSULIN_INTENT_TIER_LOOKUP:
        if tier is not None:
            return f"{product_title} is tier {tier} on plan {plan_key}."
        return (
            f"{product_title} does not have a published tier on plan {plan_key} in CMS data."
        )

    channels = data.get("channels") or {}
    if intent == INSULIN_INTENT_CHANNEL_CONTRAST and channels:
        mail = _channel_amount(channels, "preferred_mail")
        if mail is None:
            mail = _channel_amount(channels, "standard_mail")
        retail = _channel_amount(channels, "preferred_retail")
        if retail is None:
            retail = _channel_amount(channels, "standard_retail")
        parts: list[str] = []
        if mail is not None:
            parts.append(
                f"{PHARMACY_CHANNEL_LABELS['preferred_mail']} is {_format_currency(mail)}"
            )
        if retail is not None:
            parts.append(
                f"{PHARMACY_CHANNEL_LABELS['preferred_retail']} is {_format_currency(retail)}"
            )
        if parts:
            joined = " and ".join(parts)
            return (
                f"For a {days_supply}-day {product_title} fill on plan {plan_key}, "
                f"{joined}."
            )

    if channels:
        low, high = _cost_bounds_from_channels(channels, pharmacy_channel)
    else:
        low, high = data.get("cost_low"), data.get("cost_high")
    amount = _format_cost_amount(low, high)
    if amount is None:
        return (
            f"{product_title} has no published CMS cost-share estimate for this plan "
            "and fill size."
        )

    channel_clause = ""
    if pharmacy_channel:
        label = PHARMACY_CHANNEL_LABELS.get(pharmacy_channel, pharmacy_channel)
        channel_clause = f" at {label.lower()}"

    phase = data.get("benefit_phase") or data.get("effective_phase")
    phase_clause = ""
    if phase == "catastrophic":
        phase_clause = " in catastrophic coverage"
    elif include_phase:
        phase_label = _format_phase_label(phase)
        if phase_label:
            phase_clause = f" in {phase_label}"

    if intent == INSULIN_INTENT_TIER_LOOKUP and tier is not None:
        return f"{product_title} is tier {tier} on plan {plan_key}."
    if intent == INSULIN_INTENT_POLICY_CEILING and low is not None and low < 35:
        return (
            f"{product_title} is estimated at {amount} for a {days_supply}-day supply"
            f"{channel_clause} on plan {plan_key}{phase_clause} — below the federal $35 "
            "insulin ceiling."
        )
    return (
        f"{product_title} is estimated at {amount} for a {days_supply}-day supply"
        f"{channel_clause} on plan {plan_key}{phase_clause}."
    )
