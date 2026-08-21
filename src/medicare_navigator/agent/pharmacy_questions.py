"""Deterministic parsing and prose for chat-driven pharmacy locator questions.

Handles three question types, each requiring a ZIP code parsed out of free chat text — the
UI's ZIP picker is discovery-only and never reaches the chat backend (see
frontend/src/app.js's filters comment), and this feature is chat-only by design, so ZIP must
come from the message itself. This ZIP is a different concept from tools/zip_lookup.py's
discovery-only ZIP3->state table and must never influence drug-cost math — see
ingestion/zip_centroids.py's docstring.

1. Preferred pharmacies for a ZIP + plan (resolve_preferred_pharmacy_question)
2. Drug cost at the nearest preferred-retail pharmacy for a ZIP + plan
   (resolve_pharmacy_cost_question) — reuses estimate_drug_cost_all_channels unmodified;
   CMS prices at the channel level, not per individual pharmacy, so "nearest preferred
   pharmacy" always means preferred_retail, never preferred_mail (mail-order has no
   meaningful physical proximity).
3. Nearby pharmacies for a ZIP, no plan required, optionally scoped to a named plan and/or a
   mail-order vs. retail channel family (resolve_nearby_pharmacy_question)

Each resolver returns (explanation, tool_artifacts, tools_invoked, status) or None to defer
to the LLM agent loop, which can also call find_pharmacies itself for phrasing these regexes
miss. status is "ok" or "needs_clarification" (ask for a missing ZIP/plan/dosage rather than
guess one).
"""

from __future__ import annotations

import re
from typing import Any

from medicare_navigator.agent.dosage_questions import (
    _mentioned_common_drugs,
    build_dosage_clarification_explanation,
    drugs_missing_dosage,
)
from medicare_navigator.agent.insulin_requests import (
    format_insulin_estimate_sentence,
    mentioned_oral_drugs_with_strength,
)
from medicare_navigator.agent.oop_questions import extract_plan_key
from medicare_navigator.mcp.registry import serialize_tool_result
from medicare_navigator.models.response import PharmacyResult
from medicare_navigator.models.tool_result import ToolStatus
from medicare_navigator.tools.estimate_drug_cost import estimate_drug_cost_all_channels
from medicare_navigator.tools.pharmacy_lookup import find_pharmacies

_PREFERRED_PHARMACY_RE = re.compile(r"\bpreferred\s+pharmac", re.I)
_NEARBY_PHARMACY_RE = re.compile(
    r"\bpharmac(?:y|ies)\b[^\n.,;]{0,30}\b(?:near|nearby|close|around)\b|"
    r"\b(?:near|nearby|close|around)\b[^\n.,;]{0,30}\bpharmac",
    re.I,
)
_ZIP_KEYWORD_RE = re.compile(r"\bzip(?:\s*code)?\b\D{0,20}?(\d{5})\b", re.I)
_BARE_ZIP_RE = re.compile(r"\b(\d{5})\b")
_LIVE_IN_ZIP_RE = re.compile(
    r"\b(?:i\s+live\s+in|i'm\s+in|i\s+am\s+in|my\s+address\s+is)\s+(\d{5})\b",
    re.I,
)
_RADIUS_FOLLOW_UP_RE = re.compile(
    r"\b(?:within|check|search|look|try|expand|widen|radius|range)\b[^\n.]{0,40}\b\d+\s*miles?\b|"
    r"\b\d+\s*miles?\b[^\n.]{0,20}\b(?:instead|radius)\b",
    re.I,
)
DEFAULT_PHARMACY_SEARCH_RADIUS_MILES = 25
_MAIL_ORDER_RE = re.compile(r"\bmail[-\s]?order\b|\bby mail\b|\bmail\s+pharmac", re.I)
_RETAIL_ONLY_RE = re.compile(r"\bretail\b", re.I)
_CHANNEL_NEGATION_RE = re.compile(r"\b(?:not|no|except|excluding)\b", re.I)

_MISSING_ZIP_MESSAGE = "What ZIP code are you in? I need that to find pharmacies near you."
_MISSING_PLAN_MESSAGE = (
    "Which Medicare plan are you asking about? I need the plan to check its "
    "pharmacy network."
)


def is_preferred_pharmacy_question(message: str) -> bool:
    return bool(_PREFERRED_PHARMACY_RE.search(message))


def is_nearby_pharmacy_question(message: str) -> bool:
    return bool(_NEARBY_PHARMACY_RE.search(message))


def extract_zip(message: str) -> str | None:
    match = _ZIP_KEYWORD_RE.search(message)
    if match:
        return match.group(1)
    if re.search(r"\bzip\b", message, re.I):
        match = _BARE_ZIP_RE.search(message)
        if match:
            return match.group(1)
    live_in = _LIVE_IN_ZIP_RE.search(message)
    if live_in:
        return live_in.group(1)
    return None


def build_find_pharmacies_session_call(
    message: str,
    *,
    filter_plan_id: str | None = None,
    preferred_only: bool = False,
    channel: str | None = None,
    limit: int | None = None,
) -> dict[str, Any] | None:
    """Session context for pharmacy follow-ups (e.g. radius-widen requests)."""
    zip_code = extract_zip(message)
    if not zip_code:
        return None
    arguments: dict[str, Any] = {"zip_code": zip_code}
    plan_key = extract_plan_key(message) or filter_plan_id
    if plan_key:
        arguments["plan_key"] = plan_key
    if preferred_only:
        arguments["preferred_only"] = True
    if channel:
        arguments["channel"] = channel
    if limit is not None:
        arguments["limit"] = limit
    return {"name": "find_pharmacies", "arguments": arguments}


def _pharmacy_context_from_last_tool_calls(
    last_tool_calls: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    for call in reversed(last_tool_calls or []):
        if call.get("name") == "find_pharmacies":
            return call.get("arguments") or {}
    return None


def resolve_pharmacy_radius_follow_up(
    message: str,
    last_tool_calls: list[dict[str, Any]] | None,
) -> tuple[str, dict[str, Any], list[str], str] | None:
    """Honest refusal when a follow-up asks to widen the search radius — chat has no path
    to re-run find_pharmacies at a different radius today."""
    if not _RADIUS_FOLLOW_UP_RE.search(message):
        return None
    context = _pharmacy_context_from_last_tool_calls(last_tool_calls)
    if context is None:
        return None

    zip_code = context.get("zip_code") or "your ZIP"
    radius = DEFAULT_PHARMACY_SEARCH_RADIUS_MILES
    explanation = (
        f"I can't widen the pharmacy search radius in chat — lookups use a fixed "
        f"{radius:.0f}-mile range from ZIP {zip_code}. "
        f"No pharmacies were found within that range."
    )
    return explanation, {}, [], "ok"


def _extract_channel_scope(message: str) -> str | None:
    """'mail' or 'retail' channel-family scope from free text, or None for no filter.

    find_pharmacies' own ``channel`` param needs an exact preferred/standard + retail/mail
    string; the user's wording only ever signals the mail-vs-retail half, so this filters
    PharmacyResult.channel by suffix after the fact rather than passing channel= through.

    A negation word (not/no/except/excluding) immediately before a channel mention flips it:
    "retail only, not mail order" must resolve to "retail", not match the literal "mail
    order" substring and return "mail".
    """

    def _negated(match: re.Match[str]) -> bool:
        window = message[max(0, match.start() - 15) : match.start()]
        return bool(_CHANNEL_NEGATION_RE.search(window))

    mail_match = _MAIL_ORDER_RE.search(message)
    retail_match = _RETAIL_ONLY_RE.search(message)
    wants_mail = bool(mail_match) and not _negated(mail_match)
    wants_retail = bool(retail_match) and not _negated(retail_match)

    if mail_match and _negated(mail_match) and not retail_match:
        return "retail"
    if retail_match and _negated(retail_match) and not mail_match:
        return "mail"
    if wants_mail and not wants_retail:
        return "mail"
    if wants_retail and not wants_mail:
        return "retail"
    return None


def _extract_drug_dosage_pairs(message: str) -> dict[str, str | None]:
    pairs: dict[str, str | None] = dict(mentioned_oral_drugs_with_strength(message))
    for drug in _mentioned_common_drugs(message):
        pairs.setdefault(drug, None)
    return pairs


def _add_nppes_artifact(tool_artifacts: dict[str, Any], as_of_date: str) -> None:
    """Synthetic artifact so build_citations_from_artifacts' NPPES citation survives
    apply_guardrails' source-id traceability filter (extract_source_ids reads every
    artifact's source_id, not just find_pharmacies') — every returned PharmacyResult is,
    by construction, NPPES-enriched."""
    tool_artifacts["nppes_npi_registry"] = {
        "status": "ok",
        "source_id": "nppes_npi_registry",
        "as_of_date": as_of_date,
        "message": None,
        "data": None,
    }


def _pharmacy_list_sentence(pharmacies: list[PharmacyResult]) -> str:
    lines: list[str] = []
    for p in pharmacies:
        label = p.pharmacy_name or (f"Pharmacy near {p.zip_code}" if p.zip_code else "Pharmacy")
        parts = [label]
        addr_bits = [b for b in (p.address_line1, p.city, p.state) if b]
        if addr_bits:
            parts.append(", ".join(addr_bits))
        if p.zip_code:
            parts.append(p.zip_code)
        if p.distance_miles is not None:
            parts.append(f"{p.distance_miles} mi away")
        lines.append("- " + " — ".join(parts))
    return "\n".join(lines)


def resolve_preferred_pharmacy_question(
    message: str,
    *,
    filter_plan_id: str | None = None,
) -> tuple[str, dict[str, Any], list[str], str] | None:
    """Q1: which pharmacies are in my plan's preferred network, near my ZIP."""
    if not is_preferred_pharmacy_question(message):
        return None

    zip_code = extract_zip(message)
    if not zip_code:
        return _MISSING_ZIP_MESSAGE, {}, [], "needs_clarification"

    plan_key = extract_plan_key(message) or filter_plan_id
    if not plan_key:
        return _MISSING_PLAN_MESSAGE, {}, [], "needs_clarification"

    result = find_pharmacies(zip_code=zip_code, plan_key=plan_key, preferred_only=True)
    artifact = serialize_tool_result(result)
    tool_artifacts = {"find_pharmacies": artifact}

    if result.status != ToolStatus.ok or not result.data:
        explanation = result.message or (
            f"No preferred pharmacies found near ZIP {zip_code} for plan {plan_key}."
        )
        return explanation, tool_artifacts, ["find_pharmacies"], "ok"

    _add_nppes_artifact(tool_artifacts, artifact.get("as_of_date", ""))
    explanation = (
        f"Preferred pharmacies near ZIP {zip_code} in plan {plan_key}'s network:\n\n"
        f"{_pharmacy_list_sentence(result.data)}"
    )
    return explanation, tool_artifacts, ["find_pharmacies"], "ok"


def resolve_nearby_pharmacy_question(
    message: str,
    *,
    filter_plan_id: str | None = None,
) -> tuple[str, dict[str, Any], list[str], str] | None:
    """Q3: any pharmacy near my ZIP. Plan is optional (unlike Q1/Q2) — when the message or UI
    filter names one, results are scoped to that plan's network; a mail-order/retail wording
    cue additionally narrows to that channel family. Neither is required to answer."""
    if not is_nearby_pharmacy_question(message):
        return None
    if is_preferred_pharmacy_question(message):
        # e.g. "nearest preferred pharmacies" is the plan-scoped question, handled by
        # resolve_preferred_pharmacy_question — this resolver is plan-agnostic only.
        return None

    zip_code = extract_zip(message)
    if not zip_code:
        return _MISSING_ZIP_MESSAGE, {}, [], "needs_clarification"

    plan_key = extract_plan_key(message) or filter_plan_id
    channel_scope = _extract_channel_scope(message)
    scope_suffix = f" in plan {plan_key}'s network" if plan_key else ""

    result = find_pharmacies(zip_code=zip_code, plan_key=plan_key)
    artifact = serialize_tool_result(result)
    tool_artifacts = {"find_pharmacies": artifact}

    if result.status != ToolStatus.ok or not result.data:
        explanation = result.message or f"No pharmacies found near ZIP {zip_code}{scope_suffix}."
        return explanation, tool_artifacts, ["find_pharmacies"], "ok"

    pharmacies = result.data
    label = "Pharmacies"
    if channel_scope:
        pharmacies = [p for p in pharmacies if (p.channel or "").endswith(f"_{channel_scope}")]
        label = "Mail-order pharmacies" if channel_scope == "mail" else "Retail pharmacies"
        if not pharmacies:
            explanation = (
                f"No {label.lower()} found near ZIP {zip_code}{scope_suffix}."
            )
            return explanation, tool_artifacts, ["find_pharmacies"], "ok"

    _add_nppes_artifact(tool_artifacts, artifact.get("as_of_date", ""))
    explanation = f"{label} near ZIP {zip_code}{scope_suffix}:\n\n{_pharmacy_list_sentence(pharmacies)}"
    return explanation, tool_artifacts, ["find_pharmacies"], "ok"


async def resolve_pharmacy_cost_question(
    message: str,
    *,
    filter_plan_id: str | None = None,
    filter_days_supply: int | None = None,
    filter_ytd_oop_spend: float | None = None,
    filter_drug: str | None = None,
    filter_dosage: str | None = None,
) -> tuple[str, dict[str, Any], list[str], str] | None:
    """Q2: drug cost at my nearest preferred-retail pharmacy for a ZIP + plan.

    Reuses estimate_drug_cost_all_channels unmodified — no new cost math. Only fires when a
    drug is actually named alongside "preferred pharmacy" wording; a bare "what are my
    preferred pharmacies" question defers to resolve_preferred_pharmacy_question.
    """
    if not is_preferred_pharmacy_question(message):
        return None

    drug_pairs = _extract_drug_dosage_pairs(message)
    if not drug_pairs:
        return None

    zip_code = extract_zip(message)
    if not zip_code:
        return (
            "What ZIP code are you in? I need that to find your preferred pharmacy before "
            "estimating cost.",
            {},
            [],
            "needs_clarification",
        )

    plan_key = extract_plan_key(message) or filter_plan_id
    if not plan_key:
        return _MISSING_PLAN_MESSAGE, {}, [], "needs_clarification"

    missing = drugs_missing_dosage(message, filter_drug=filter_drug, filter_dosage=filter_dosage)
    if missing:
        explanation = await build_dosage_clarification_explanation(missing)
        return explanation, {}, [], "needs_clarification"

    pharmacy_result = find_pharmacies(
        zip_code=zip_code,
        plan_key=plan_key,
        preferred_only=True,
        channel="preferred_retail",
        limit=1,
    )
    pharmacy_artifact = serialize_tool_result(pharmacy_result)
    tool_artifacts: dict[str, Any] = {"find_pharmacies": pharmacy_artifact}
    tools_invoked = ["find_pharmacies"]

    if pharmacy_result.status != ToolStatus.ok or not pharmacy_result.data:
        explanation = pharmacy_result.message or (
            f"No preferred-retail pharmacy found near ZIP {zip_code} for plan {plan_key}."
        )
        return explanation, tool_artifacts, tools_invoked, "ok"

    _add_nppes_artifact(tool_artifacts, pharmacy_artifact.get("as_of_date", ""))
    pharmacy = pharmacy_result.data[0]
    days_supply = filter_days_supply if filter_days_supply is not None else 30
    ytd_oop_spend = filter_ytd_oop_spend if filter_ytd_oop_spend is not None else 0.0

    pharmacy_bits = [pharmacy.pharmacy_name]
    if pharmacy.address_line1:
        pharmacy_bits.append(pharmacy.address_line1)
    if pharmacy.city:
        pharmacy_bits.append(f"{pharmacy.city} {pharmacy.zip_code or ''}".strip())
    if pharmacy.distance_miles is not None:
        pharmacy_bits.append(f"{pharmacy.distance_miles} mi from {zip_code}")
    lines = [f"Nearest preferred-retail pharmacy: {', '.join(pharmacy_bits)}."]

    calls: list[dict[str, Any]] = []
    any_needs_dosage = False
    for drug, dosage in drug_pairs.items():
        estimate = await estimate_drug_cost_all_channels(
            plan_key=plan_key,
            drug_name=drug,
            dosage=dosage,
            days_supply=days_supply,
            ytd_oop_spend=ytd_oop_spend,
        )
        artifact = serialize_tool_result(estimate)
        calls.append(artifact)
        if artifact["status"] == "needs_dosage":
            any_needs_dosage = True
        lines.append(
            format_insulin_estimate_sentence(
                product=drug,
                plan_key=plan_key,
                days_supply=days_supply,
                artifact=artifact,
                pharmacy_channel="preferred_retail",
            )
        )

    lines.append(
        "CMS prices this fill at the preferred-retail channel level — the dollar amount is "
        f"the same at every preferred-retail pharmacy in {plan_key}'s network, not specific "
        f"to {pharmacy.pharmacy_name} individually."
    )

    tool_artifacts["estimate_drug_cost_all_channels"] = calls[-1]
    tool_artifacts["estimate_drug_cost_all_channels__calls"] = calls
    tools_invoked.append("estimate_drug_cost_all_channels")

    status = "needs_clarification" if any_needs_dosage else "ok"
    return "\n\n".join(lines), tool_artifacts, tools_invoked, status
