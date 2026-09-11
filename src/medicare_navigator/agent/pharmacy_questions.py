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
4. Which plans (in the ZIP's state) cover a named drug AND have a preferred pharmacy nearby
   (resolve_plan_pharmacy_match_question) — a real cross-reference of formulary coverage and
   pharmacy-network proximity across every candidate plan, not just one already-named plan.
   Pharmacy location data has no drug-stocking info at all ("which pharmacies carry this
   drug" is unanswerable by design), so this only ever reports plan-level coverage plus
   plan-network pharmacy proximity — never a per-pharmacy drug claim.
5. Which plans (in the ZIP's state) cover a named drug, priced, with no pharmacy-network
   angle at all (resolve_plan_coverage_question) — the plain "what plans cover my drug in my
   zip" question, distinct from Q4 in that it never requires "pharmacy" wording. Without this
   resolver such a message matches none of Q1-Q4 (all gated on pharmacy wording) and falls
   through to the LLM agent loop, which calls the unbounded list_plans tool and can end up
   pricing and enumerating every plan on file for the state — this resolver keeps that answer
   free and bounded, mirroring Q4's plumbing minus the pharmacy join.

Each resolver returns (explanation, tool_artifacts, tools_invoked, status) or None to defer
to the LLM agent loop, which can also call find_pharmacies itself for phrasing these regexes
miss. status is "ok" or "needs_clarification" (ask for a missing ZIP/plan/dosage rather than
guess one).
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from medicare_navigator.agent.dosage_questions import (
    _mentioned_common_drugs,
    build_dosage_clarification_explanation,
    drugs_missing_dosage,
)
from medicare_navigator.agent.insulin_requests import (
    _extract_products as _extract_insulin_products,
    format_insulin_estimate_sentence,
    mentioned_oral_drugs_with_strength,
)
from medicare_navigator.agent.mixed_basket_requests import batch_result_to_artifact
from medicare_navigator.agent.oop_questions import extract_plan_key, extract_plan_keys
from medicare_navigator.guardrails.channel_parity import cost_sentence_for_estimate
from medicare_navigator.mcp.registry import serialize_tool_result
from medicare_navigator.models.response import PharmacyResult
from medicare_navigator.models.tool_result import ToolResult, ToolStatus
from medicare_navigator.storage.repository import PlanRepository
from medicare_navigator.tools.batch_estimate import BatchEstimateRequest, run_batch_estimates
from medicare_navigator.tools.estimate_drug_cost import (
    check_formulary_coverage_for_plans,
    estimate_drug_cost_all_channels,
    resolve_drug_for_pricing,
)
from medicare_navigator.tools.pharmacy_lookup import find_pharmacies, is_zip_only_stub_pharmacy
from medicare_navigator.tools.zip_lookup import zip_to_state

_PREFERRED_PHARMACY_RE = re.compile(r"\bpreferred\s+pharmac", re.I)
_NEARBY_PHARMACY_RE = re.compile(
    r"\bpharmac(?:y|ies)\b[^\n.,;]{0,30}\b(?:near|nearby|close|around)\b|"
    r"\b(?:near|nearby|close|around)\b[^\n.,;]{0,30}\bpharmac|"
    r"\bpharmac(?:y|ies)\b[^\n.,;]{0,40}\b(?:in|available in)\b[^\n.,;]{0,25}\b(?:my\s+)?zip\b|"
    r"\bpharmacies\b[^\n.,;]{0,30}\bin\b[^\n.,;]{0,15}\b\d{5}\b",
    re.I,
)
_PHARMACY_IN_ZIP_DIGITS_RE = re.compile(
    r"\bpharmacies\b[^\n.,;]{0,30}\bin\b[^\n.,;]{0,15}\b(\d{5})\b",
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
# Q4 bounds: coverage-checking every candidate plan is cheap (one DB lookup per distinct
# formulary_id, see check_formulary_coverage_for_plans), but the pharmacy-proximity check is
# a real per-plan join+haversine scan, so it's capped; any truncation is disclosed in prose.
MAX_COVERED_PLANS_FOR_PHARMACY_CHECK = 10
MAX_DISPLAYED_PLAN_MATCHES = 5
# Q5 bounds: pricing a covered plan is a cheap DB lookup, but each priced plan can add a
# channel-coverage disclaimer sentence to the reply (guardrails/channel_parity.py), so both
# how many get priced and how many get displayed are capped independently; any truncation is
# disclosed in prose.
MAX_PRICED_PLANS_FOR_COVERAGE = 8
MAX_DISPLAYED_PLAN_COVERAGE = 5
_PLAN_COVERAGE_RE = re.compile(
    r"\bplans?\b[^\n.,;]{0,60}\b(?:cover|covers|covering|include|includes)\b|"
    r"\b(?:cover|covers|covering)\b[^\n.,;]{0,60}\bplans?\b",
    re.I,
)
_MAIL_ORDER_RE = re.compile(r"\bmail[-\s]?order\b|\bby mail\b|\bmail\s+pharmac", re.I)
_RETAIL_ONLY_RE = re.compile(r"\bretail\b", re.I)
_CHANNEL_NEGATION_RE = re.compile(r"\b(?:not|no|except|excluding)\b", re.I)

_NETWORK_ANCHOR_RE = re.compile(r"\bnetwork\b|\bpharmac", re.I)


def _extract_plan_key_for_pharmacy(message: str, filter_plan_id: str | None) -> str | None:
    """Plan key for a pharmacy-network answer, disambiguated when more than one plan is
    named in the same message.

    extract_plan_key always returns the *first* plan-key-shaped token, which is wrong for
    a message like "Compare lantus on S9999-001 vs H8888-001, and what pharmacies are in
    H8888-001's network?" — the pharmacy question is about the second plan, not the first.
    When multiple plan keys appear, pick whichever mention sits closest to a
    "network"/"pharmac..." anchor word instead; fall back to the first-match behavior when
    there's zero or one plan key, or no anchor to disambiguate with.
    """
    matches = extract_plan_keys(message)
    if len(matches) <= 1:
        return extract_plan_key(message) or filter_plan_id
    anchors = [m.start() for m in _NETWORK_ANCHOR_RE.finditer(message)]
    if not anchors:
        return extract_plan_key(message) or filter_plan_id
    nearest = min(matches, key=lambda m: min(abs(m.start() - a) for a in anchors))
    return nearest.group(0).upper()


def message_names_priceable_drug(message: str) -> bool:
    """True when the message names at least one drug this file's resolvers can price
    (oral, with strength, or insulin). Used by navigator.py to decide whether a
    duration/date-window signal must also suppress the plan-scoped pharmacy resolvers —
    not just the drug-cost one — so a compound "cost for the next 3 months, and any
    preferred pharmacies nearby?" question doesn't fall through Q2 only to have Q1
    silently answer the pharmacy half alone and still drop the multi-month cost ask."""
    return bool(_extract_drug_dosage_pairs(message))


_MISSING_ZIP_MESSAGE = "What ZIP code are you in? I need that to find pharmacies near you."
_MISSING_PLAN_MESSAGE = (
    "Which Medicare plan are you asking about? I need the plan to check its "
    "pharmacy network."
)

_PHARMACY_VOCAB = ("pharmacy", "pharmacies")


def _normalize_pharmacy_typos(message: str) -> str:
    """Map near-miss spellings of "pharmacy"/"pharmacies" (e.g. "pharamacies") to the
    canonical form so the pharmac* regexes below can still match. Only used as a fallback
    when the plain regex misses — see is_preferred_pharmacy_question/is_nearby_pharmacy_question."""

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if "pharmac" in token.lower():
            return token
        close = difflib.get_close_matches(token.lower(), _PHARMACY_VOCAB, n=1, cutoff=0.8)
        return close[0] if close else token

    return re.sub(r"[A-Za-z]{7,}", _replace, message)


def is_preferred_pharmacy_question(message: str) -> bool:
    if _PREFERRED_PHARMACY_RE.search(message):
        return True
    return bool(_PREFERRED_PHARMACY_RE.search(_normalize_pharmacy_typos(message)))


def is_nearby_pharmacy_question(message: str) -> bool:
    if _NEARBY_PHARMACY_RE.search(message):
        return True
    return bool(_NEARBY_PHARMACY_RE.search(_normalize_pharmacy_typos(message)))


def is_plan_coverage_question(message: str) -> bool:
    return bool(_PLAN_COVERAGE_RE.search(message))


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
    pharmacy_in_zip = _PHARMACY_IN_ZIP_DIGITS_RE.search(message)
    if pharmacy_in_zip:
        return pharmacy_in_zip.group(1)
    return None


def find_pharmacies_had_results(tool_artifacts: dict[str, Any]) -> bool:
    """True when the most recent find_pharmacies artifact returned at least one pharmacy."""
    artifact = tool_artifacts.get("find_pharmacies") or {}
    if artifact.get("status") != "ok":
        return False
    data = artifact.get("data")
    if data is None:
        return False
    if isinstance(data, list):
        return len(data) > 0
    return bool(data)


def build_find_pharmacies_session_call(
    message: str,
    *,
    filter_plan_id: str | None = None,
    preferred_only: bool = False,
    channel: str | None = None,
    limit: int | None = None,
    had_results: bool | None = None,
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
    if had_results is not None:
        arguments["had_results"] = had_results
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
    had_results = context.get("had_results")
    explanation = (
        f"I can't widen the pharmacy search radius in chat — lookups use a fixed "
        f"{radius:.0f}-mile range from ZIP {zip_code}."
    )
    if had_results is True:
        explanation += (
            " The pharmacies listed in my previous reply are still the nearest matches "
            "within that range."
        )
    elif had_results is False:
        explanation += " No pharmacies were found within that range."
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
    """Every named drug this file's Q2/Q4/Q5 resolvers can price, oral or insulin.

    mentioned_oral_drugs_with_strength excludes insulin products by design (insulin_requests.py
    owns dosage-free insulin pricing) — without this, an insulin product named alongside
    "preferred pharmacy" wording reads as drug-less here, Q2 defers, and Q1 (which doesn't gate
    on a drug at all) answers with a bare pharmacy list that silently drops the cost question.
    estimate_drug_cost_all_channels already prices insulin correctly with dosage=None, matching
    how agent/navigator.py's insulin path calls it.
    """
    pairs: dict[str, str | None] = dict(mentioned_oral_drugs_with_strength(message))
    for drug in _mentioned_common_drugs(message):
        pairs.setdefault(drug, None)
    for drug in _extract_insulin_products(message):
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


def _format_pharmacy_distance_suffix(distance_miles: float | None) -> str | None:
    """Omit zero-mile ZIP-centroid matches from user-facing prose."""
    if distance_miles is None or distance_miles <= 0:
        return None
    return f"{distance_miles:g} mi away"


def _pharmacy_results_header(
    *,
    zip_code: str,
    label: str,
    radius_miles: float = DEFAULT_PHARMACY_SEARCH_RADIUS_MILES,
    scope_suffix: str = "",
) -> str:
    radius = f"{radius_miles:g}"
    return f"{label} within {radius} miles of ZIP {zip_code}{scope_suffix}:"


def _is_zip_only_stub(pharmacy: PharmacyResult) -> bool:
    return is_zip_only_stub_pharmacy(
        pharmacy_name=pharmacy.pharmacy_name,
        address_line1=pharmacy.address_line1,
        zip_code=pharmacy.zip_code,
    )


def _pharmacy_list_sentence(pharmacies: list[PharmacyResult]) -> str:
    if pharmacies and all(_is_zip_only_stub(p) for p in pharmacies):
        zip_code = pharmacies[0].zip_code or "your ZIP"
        count = len(pharmacies)
        noun = "pharmacy" if count == 1 else "pharmacies"
        return (
            f"CMS lists {count} in-network {noun} in ZIP {zip_code}, but name and street "
            f"address are not available in the returned data."
        )

    lines: list[str] = []
    seen: set[str] = set()
    for p in pharmacies:
        label = p.pharmacy_name or (f"Pharmacy near {p.zip_code}" if p.zip_code else "Pharmacy")
        parts = [label]
        addr_bits = [b for b in (p.address_line1, p.city, p.state) if b]
        if addr_bits:
            parts.append(", ".join(addr_bits))
        if p.zip_code:
            parts.append(p.zip_code)
        distance_suffix = _format_pharmacy_distance_suffix(p.distance_miles)
        if distance_suffix:
            parts.append(distance_suffix)
        line = "- " + " — ".join(parts)
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
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

    plan_key = _extract_plan_key_for_pharmacy(message, filter_plan_id)
    if not plan_key:
        return _MISSING_PLAN_MESSAGE, {}, [], "needs_clarification"

    result = find_pharmacies(zip_code=zip_code, plan_key=plan_key, preferred_only=True)
    artifact = serialize_tool_result(result)
    tool_artifacts = {"find_pharmacies": artifact}

    if result.status != ToolStatus.ok or not result.data:
        explanation = result.message or (
            f"No preferred pharmacies found within {DEFAULT_PHARMACY_SEARCH_RADIUS_MILES:g} miles "
            f"of ZIP {zip_code} for plan {plan_key}."
        )
        return explanation, tool_artifacts, ["find_pharmacies"], "ok"

    _add_nppes_artifact(tool_artifacts, artifact.get("as_of_date", ""))
    scope_suffix = f" in plan {plan_key}'s network"
    explanation = (
        f"{_pharmacy_results_header(zip_code=zip_code, label='Preferred pharmacies', scope_suffix=scope_suffix)}\n\n"
        f"{_pharmacy_list_sentence(result.data)}"
    )
    return explanation, tool_artifacts, ["find_pharmacies"], "ok"


async def resolve_plan_pharmacy_match_question(
    message: str,
    *,
    filter_plan_id: str | None = None,
    filter_drug: str | None = None,
    filter_dosage: str | None = None,
) -> tuple[str, dict[str, Any], list[str], str] | None:
    """Q4: which Medicare plans cover a named drug AND have a preferred pharmacy near my
    ZIP — a real cross-reference of basic_drugs_formulary and pharmacy_network across every
    candidate plan in the ZIP's state, not just a ZIP-only pharmacy list (Q3) or a single
    named plan's cost/network (Q1/Q2).

    Guard: fires only for a Q3-shaped ("near/nearby me") message naming exactly one drug,
    with no plan already known — if a plan is named, Q1/Q2 already own that plan-scoped
    question. Defers (returns None) whenever it can't even attempt the cross-reference (ZIP
    maps to no recognized state, or that state has no ingested plan data) so
    resolve_nearby_pharmacy_question's plain, if unfiltered, pharmacy list can still answer
    — its own drug-caveat addition covers that case.
    """
    if not is_nearby_pharmacy_question(message):
        return None
    if is_preferred_pharmacy_question(message):
        return None
    if extract_plan_key(message) or filter_plan_id:
        return None

    drug_pairs = _extract_drug_dosage_pairs(message)
    if not drug_pairs or len(drug_pairs) > 1:
        # No drug named, or more than one (a different, unimplemented question) — defer.
        return None
    drug_name, dosage = next(iter(drug_pairs.items()))

    zip_code = extract_zip(message)
    if not zip_code:
        return _MISSING_ZIP_MESSAGE, {}, [], "needs_clarification"

    state = zip_to_state(zip_code)
    if state is None:
        return None

    missing = drugs_missing_dosage(message, filter_drug=filter_drug, filter_dosage=filter_dosage)
    if missing:
        explanation = await build_dosage_clarification_explanation(missing)
        return explanation, {}, [], "needs_clarification"

    resolved = await resolve_drug_for_pricing(drug_name, dosage)
    if isinstance(resolved, ToolResult):
        status = "needs_clarification" if resolved.status == ToolStatus.needs_dosage else "ok"
        return resolved.message or f"I couldn't resolve '{drug_name}'.", {}, [], status

    candidate_plans = [
        p for p in PlanRepository().list_plans(state=state) if not p["plan_suppressed"]
    ]
    if not candidate_plans:
        return None

    drug_label = resolved.resolved_drug_name + (
        f" {resolved.resolved_dosage}" if resolved.resolved_dosage else ""
    )

    formulary_ids = [p["formulary_id"] for p in candidate_plans if p.get("formulary_id")]
    coverage = await check_formulary_coverage_for_plans(
        formulary_ids=formulary_ids,
        rxcui=resolved.rxcui,
        drug_name=resolved.resolved_drug_name,
        dosage=resolved.resolved_dosage,
    )
    covered_plans = [
        p
        for p in candidate_plans
        if p.get("formulary_id") and coverage.get(p["formulary_id"], ([], ""))[0]
    ]

    if not covered_plans:
        return (
            f"None of the {len(candidate_plans)} Medicare plan(s) I have on file for "
            f"{state} (from ZIP {zip_code}) cover {drug_label}.",
            {},
            [],
            "ok",
        )

    checked = covered_plans[:MAX_COVERED_PLANS_FOR_PHARMACY_CHECK]
    truncated_coverage = len(covered_plans) - len(checked)

    matches_with_pharmacy: list[tuple[dict[str, Any], PharmacyResult]] = []
    covered_without_pharmacy: list[dict[str, Any]] = []
    pharmacy_calls: list[ToolResult] = []
    for plan in checked:
        result = find_pharmacies(
            zip_code=zip_code,
            plan_key=plan["plan_key"],
            preferred_only=True,
            channel="preferred_retail",
            limit=1,
        )
        pharmacy_calls.append(result)
        if result.status == ToolStatus.ok and result.data:
            matches_with_pharmacy.append((plan, result.data[0]))
        else:
            covered_without_pharmacy.append(plan)

    tool_artifacts: dict[str, Any] = {}
    tools_invoked = ["find_pharmacies"]
    if matches_with_pharmacy:
        seen_npi: set[str] = set()
        combined: list[PharmacyResult] = []
        for _, pharmacy in matches_with_pharmacy:
            if pharmacy.npi in seen_npi:
                continue
            seen_npi.add(pharmacy.npi)
            combined.append(pharmacy)
        first_ok = next(r for r in pharmacy_calls if r.status == ToolStatus.ok)
        combined_result = ToolResult.ok(
            combined, source_id=first_ok.source_id, as_of_date=first_ok.as_of_date
        )
        artifact = serialize_tool_result(combined_result)
        tool_artifacts["find_pharmacies"] = artifact
        _add_nppes_artifact(tool_artifacts, artifact.get("as_of_date", ""))
    else:
        # No covered plan had a nearby preferred pharmacy — still record one representative
        # failed call so citations/tool_statuses stay traceable, matching Q1/Q3's pattern.
        tool_artifacts["find_pharmacies"] = serialize_tool_result(pharmacy_calls[0])

    if not matches_with_pharmacy:
        lines = [
            f"{drug_label} is covered by {len(covered_plans)} Medicare plan(s) in {state}, "
            f"but none show a preferred-retail pharmacy within "
            f"{DEFAULT_PHARMACY_SEARCH_RADIUS_MILES:g} miles of ZIP {zip_code}:",
        ]
        lines.extend(f"- {p['plan_name']} ({p['plan_key']})" for p in checked)
        if truncated_coverage:
            lines.append(
                f"({truncated_coverage} more covered plan(s) in {state} weren't checked for "
                "a nearby pharmacy — ask about a specific plan to check it.)"
            )
        return "\n".join(lines), tool_artifacts, tools_invoked, "ok"

    displayed = matches_with_pharmacy[:MAX_DISPLAYED_PLAN_MATCHES]
    lines = [
        f"Medicare plans covering {drug_label} with a preferred-retail pharmacy within "
        f"{DEFAULT_PHARMACY_SEARCH_RADIUS_MILES:g} miles of ZIP {zip_code}:",
        "",
    ]
    for plan, pharmacy in displayed:
        bits = [pharmacy.pharmacy_name]
        if pharmacy.address_line1:
            bits.append(pharmacy.address_line1)
        if pharmacy.city:
            bits.append(f"{pharmacy.city} {pharmacy.zip_code or ''}".strip())
        distance_suffix = _format_pharmacy_distance_suffix(pharmacy.distance_miles)
        if distance_suffix:
            bits.append(distance_suffix)
        lines.append(
            f"- {plan['plan_name']} ({plan['plan_key']}) — nearest preferred pharmacy: "
            f"{', '.join(bits)}"
        )

    if len(matches_with_pharmacy) > len(displayed):
        lines.append(
            f"(+{len(matches_with_pharmacy) - len(displayed)} more covered plan(s) with a "
            "nearby preferred pharmacy — ask about a specific plan for details.)"
        )
    if covered_without_pharmacy:
        names = ", ".join(f"{p['plan_name']} ({p['plan_key']})" for p in covered_without_pharmacy)
        noun = "it" if len(covered_without_pharmacy) == 1 else "them"
        lines.append(
            f"{drug_label} is also covered by {names}, but I didn't find a preferred-retail "
            f"pharmacy within {DEFAULT_PHARMACY_SEARCH_RADIUS_MILES:g} miles of ZIP {zip_code} "
            f"for {noun}."
        )
    if truncated_coverage:
        lines.append(
            f"({truncated_coverage} more plan(s) in {state} cover {drug_label} but weren't "
            "checked for a nearby pharmacy — ask about a specific plan to check it.)"
        )
    return "\n".join(lines), tool_artifacts, tools_invoked, "ok"


async def resolve_plan_coverage_question(
    message: str,
    *,
    filter_plan_id: str | None = None,
    filter_drug: str | None = None,
    filter_dosage: str | None = None,
) -> tuple[str, dict[str, Any], list[str], str] | None:
    """Q5: which Medicare plans (in the ZIP's state) cover a named drug, priced — no
    pharmacy-network angle at all. Distinct from Q4 (resolve_plan_pharmacy_match_question),
    which requires "pharmacy" wording and adds a preferred-retail proximity join; this fires
    for the plainer "what plans cover X in my zip" phrasing that never mentions a pharmacy and
    would otherwise miss every resolver in this file and fall through to the LLM agent loop,
    which calls the unbounded list_plans tool and can end up pricing and narrating every plan
    on file for the state.

    Guard: defers to Q1-Q4 whenever the message mentions pharmacy/network wording at all, to a
    named plan (Q1/Q2/OOP/Tier/the LLM's plan-comparison flow already own that), or when no ZIP
    is given — unlike Q1-Q4 this never asks the user for a missing ZIP; a location-less "which
    plans cover X" is left to the LLM to answer plan-agnostically.
    """
    if is_preferred_pharmacy_question(message) or is_nearby_pharmacy_question(message):
        return None
    if extract_plan_key(message) or filter_plan_id:
        return None
    if not is_plan_coverage_question(message):
        return None

    zip_code = extract_zip(message)
    if not zip_code:
        return None

    drug_pairs = _extract_drug_dosage_pairs(message)
    if not drug_pairs or len(drug_pairs) > 1:
        # No drug named, or more than one (a different, unimplemented question) — defer.
        return None
    drug_name, dosage = next(iter(drug_pairs.items()))

    state = zip_to_state(zip_code)
    if state is None:
        return None

    missing = drugs_missing_dosage(message, filter_drug=filter_drug, filter_dosage=filter_dosage)
    if missing:
        explanation = await build_dosage_clarification_explanation(missing)
        return explanation, {}, [], "needs_clarification"

    resolved = await resolve_drug_for_pricing(drug_name, dosage)
    if isinstance(resolved, ToolResult):
        status = "needs_clarification" if resolved.status == ToolStatus.needs_dosage else "ok"
        return resolved.message or f"I couldn't resolve '{drug_name}'.", {}, [], status

    candidate_plans = [
        p for p in PlanRepository().list_plans(state=state) if not p["plan_suppressed"]
    ]
    if not candidate_plans:
        return None

    drug_label = resolved.resolved_drug_name + (
        f" {resolved.resolved_dosage}" if resolved.resolved_dosage else ""
    )

    formulary_ids = [p["formulary_id"] for p in candidate_plans if p.get("formulary_id")]
    coverage = await check_formulary_coverage_for_plans(
        formulary_ids=formulary_ids,
        rxcui=resolved.rxcui,
        drug_name=resolved.resolved_drug_name,
        dosage=resolved.resolved_dosage,
    )
    covered_plans = [
        p
        for p in candidate_plans
        if p.get("formulary_id") and coverage.get(p["formulary_id"], ([], ""))[0]
    ]

    if not covered_plans:
        return (
            f"None of the {len(candidate_plans)} Medicare plan(s) I have on file for "
            f"{state} (from ZIP {zip_code}) cover {drug_label}.",
            {},
            [],
            "ok",
        )

    to_price = covered_plans[:MAX_PRICED_PLANS_FOR_COVERAGE]
    truncated_coverage = len(covered_plans) - len(to_price)

    batch_results = await run_batch_estimates(
        [
            BatchEstimateRequest(
                plan_key=plan["plan_key"],
                drug_name=resolved.resolved_drug_name,
                dosage=resolved.resolved_dosage,
            )
            for plan in to_price
        ]
    )

    priced: list[tuple[dict[str, Any], dict[str, Any], float]] = []
    unpriced_plans: list[dict[str, Any]] = []
    for plan, result in zip(to_price, batch_results):
        artifact = batch_result_to_artifact(result)
        cost_low = artifact.get("cost_low")
        if result.status == "ok" and cost_low is not None:
            priced.append((plan, artifact, cost_low))
        else:
            unpriced_plans.append(plan)

    if not priced:
        return (
            f"{drug_label} is covered by {len(covered_plans)} Medicare plan(s) in {state}, "
            "but CMS published cost-share data isn't available for any of them.",
            {},
            [],
            "ok",
        )

    priced.sort(key=lambda item: item[2])
    displayed = priced[:MAX_DISPLAYED_PLAN_COVERAGE]

    lines = [
        f"Medicare plans covering {drug_label} in {state} (from ZIP {zip_code}), "
        "sorted by estimated cost:",
        "",
    ]
    call_artifacts: list[dict[str, Any]] = []
    for plan, artifact, _cost_low in displayed:
        plan_label = f"{plan['plan_name']} ({plan['plan_key']})"
        sentence = cost_sentence_for_estimate(artifact.get("data") or {})
        # cost_sentence_for_estimate identifies the plan by bare plan_key only (e.g. "on
        # H1889-014") — swap in the plan name too so the reply is legible without an
        # existing plan lookup; tool_artifacts/call_artifacts stay untouched so
        # citations/guardrails keep seeing the real plan_key.
        if sentence:
            sentence = sentence.replace(f"on {plan['plan_key']} ", f"on {plan_label} ", 1)
        lines.append(f"- {sentence}" if sentence else f"- {plan_label}")
        call_artifacts.append(artifact)

    if len(priced) > len(displayed):
        lines.append(
            f"(+{len(priced) - len(displayed)} more covered plan(s) with pricing — ask "
            "about a specific plan for details.)"
        )
    if unpriced_plans:
        names = ", ".join(f"{p['plan_name']} ({p['plan_key']})" for p in unpriced_plans)
        noun = "it" if len(unpriced_plans) == 1 else "them"
        lines.append(
            f"{drug_label} is also covered by {names}, but CMS cost-share data isn't "
            f"available for {noun}."
        )
    if truncated_coverage:
        lines.append(
            f"({truncated_coverage} more plan(s) in {state} cover {drug_label} but weren't "
            "priced — ask about a specific plan to check it.)"
        )

    tool_artifacts: dict[str, Any] = {
        "estimate_drug_cost_all_channels": displayed[0][1],
        "estimate_drug_cost_all_channels__calls": call_artifacts,
    }
    return "\n".join(lines), tool_artifacts, ["estimate_drug_cost_all_channels"], "ok"


def _drug_unfiltered_caveat(message: str) -> str | None:
    """When exactly one drug is named but this is the plan-blind, coverage-blind pharmacy
    list (resolve_plan_pharmacy_match_question either didn't apply or deferred — e.g. the
    ZIP's state has no ingested plan data), say so explicitly. Fixes the confusion where a
    bare pharmacy list with no acknowledgment of the named drug reads as non-responsive."""
    pairs = _extract_drug_dosage_pairs(message)
    if len(pairs) != 1:
        return None
    drug, dosage = next(iter(pairs.items()))
    label = f"{drug} {dosage}" if dosage else drug
    return (
        f"Note: this list is not filtered by {label} coverage or any specific plan's "
        f"network — it's every CMS-network pharmacy near your ZIP. Ask which plans cover "
        f"{label} near you for a coverage-checked answer."
    )


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

    plan_key = _extract_plan_key_for_pharmacy(message, filter_plan_id)
    channel_scope = _extract_channel_scope(message)
    scope_suffix = f" in plan {plan_key}'s network" if plan_key else ""
    caveat = _drug_unfiltered_caveat(message)

    result = find_pharmacies(zip_code=zip_code, plan_key=plan_key)
    artifact = serialize_tool_result(result)
    tool_artifacts = {"find_pharmacies": artifact}

    if result.status != ToolStatus.ok or not result.data:
        explanation = result.message or (
            f"No pharmacies found within {DEFAULT_PHARMACY_SEARCH_RADIUS_MILES:g} miles "
            f"of ZIP {zip_code}{scope_suffix}."
        )
        if caveat:
            explanation = f"{explanation}\n\n{caveat}"
        return explanation, tool_artifacts, ["find_pharmacies"], "ok"

    pharmacies = result.data
    label = "Pharmacies"
    if channel_scope:
        pharmacies = [p for p in pharmacies if (p.channel or "").endswith(f"_{channel_scope}")]
        label = "Mail-order pharmacies" if channel_scope == "mail" else "Retail pharmacies"
        if not pharmacies:
            explanation = (
                f"No {label.lower()} found within {DEFAULT_PHARMACY_SEARCH_RADIUS_MILES:g} miles "
                f"of ZIP {zip_code}{scope_suffix}."
            )
            if caveat:
                explanation = f"{explanation}\n\n{caveat}"
            return explanation, tool_artifacts, ["find_pharmacies"], "ok"

    _add_nppes_artifact(tool_artifacts, artifact.get("as_of_date", ""))
    explanation = (
        f"{_pharmacy_results_header(zip_code=zip_code, label=label, scope_suffix=scope_suffix)}\n\n"
        f"{_pharmacy_list_sentence(pharmacies)}"
    )
    if caveat:
        explanation = f"{explanation}\n\n{caveat}"
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

    plan_key = _extract_plan_key_for_pharmacy(message, filter_plan_id)
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
    if pharmacy.distance_miles is not None and pharmacy.distance_miles > 0:
        pharmacy_bits.append(f"{pharmacy.distance_miles:g} mi from {zip_code}")
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
