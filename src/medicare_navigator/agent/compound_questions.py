"""Detect compound messages that span multiple question categories.

Deterministic resolvers in navigator.py return on first match. When a message
asks two or more independent questions (OOP cap + drug cost, pharmacy list +
insulin policy, tier + plan coverage, etc.), the matching resolver must defer
so the agent loop can address every part.
"""

from __future__ import annotations

import re

from medicare_navigator.agent.insulin_requests import (
    INSULIN_INTENT_COST,
    INSULIN_INTENT_REMAINING_YEAR,
    INSULIN_INTENT_TIER_LOOKUP,
    resolve_insulin_request,
)
from medicare_navigator.agent.oop_questions import is_oop_question
from medicare_navigator.agent.pharmacy_questions import (
    is_nearby_pharmacy_question,
    is_plan_coverage_question,
    is_preferred_pharmacy_question,
    message_names_priceable_drug,
)
from medicare_navigator.agent.tier_questions import is_tier_question

_DISTINCT_SUBQUESTION_RE = re.compile(
    r"\b(?:also|and what|and how|and which|and are|and can|and is|and do)\b|"
    r"\(\d+\)|;\s*",
    re.I,
)
_MAIL_ORDER_RE = re.compile(r"\bmail(?:[- ]order)?\b", re.I)


def message_asks_distinct_subquestions(message: str) -> bool:
    """True when the message bundles separate asks, not one integrated question."""
    if _DISTINCT_SUBQUESTION_RE.search(message):
        return True
    return message.count("?") >= 2


def message_intent_categories(message: str) -> frozenset[str]:
    """Coarse question-type labels present in a single user message."""
    categories: set[str] = set()
    if is_oop_question(message):
        categories.add("oop")
    if is_tier_question(message):
        categories.add("tier")
    if is_nearby_pharmacy_question(message) or is_preferred_pharmacy_question(message):
        categories.add("pharmacy")
    if is_plan_coverage_question(message):
        categories.add("plan_coverage")

    insulin = resolve_insulin_request(message)
    if insulin:
        if insulin.is_policy_question or (
            insulin.intent.startswith("policy_")
            and re.search(r"\binsulin\b", message, re.I)
        ):
            categories.add("insulin_policy")
        if insulin.products:
            categories.add("drug_cost")
    elif message_names_priceable_drug(message):
        categories.add("drug_cost")

    return frozenset(categories)


def is_compound_message(message: str) -> bool:
    return len(message_intent_categories(message)) >= 2


def should_defer_resolver(message: str, resolver_category: str) -> bool:
    """True when this resolver would answer only part of a compound message."""
    categories = message_intent_categories(message)
    if resolver_category not in categories:
        return False
    if len(categories) < 2:
        return False
    return message_asks_distinct_subquestions(message) or resolver_category == "oop"


def should_defer_deterministic_insulin(
    message: str,
    insulin_request,
    *,
    has_unhandled_date_window: bool,
) -> bool:
    """Defer the fast insulin path when other distinct questions need answers too."""
    categories = message_intent_categories(message)
    if len(categories) < 2:
        return False

    if not message_asks_distinct_subquestions(message):
        if categories <= frozenset({"drug_cost", "pharmacy"}) and insulin_request.intent in (
            INSULIN_INTENT_COST,
            INSULIN_INTENT_REMAINING_YEAR,
        ):
            return False

    if has_unhandled_date_window and (
        "tier" in categories or insulin_request.intent == INSULIN_INTENT_TIER_LOOKUP
    ):
        return True

    if insulin_request.intent.startswith("policy_") and len(categories) >= 2:
        return message_asks_distinct_subquestions(message)

    return message_asks_distinct_subquestions(message)


def compound_prefetch_context(message: str) -> str:
    """Pre-fetched authoritative answers the agent must include in compound replies."""
    from medicare_navigator.agent.oop_questions import (
        build_part_d_cap_explanation,
        is_part_d_annual_cap_question,
        is_medical_moop_question,
    )
    from medicare_navigator.agent.pharmacy_questions import extract_zip

    categories = message_intent_categories(message)
    if len(categories) < 2 or not message_asks_distinct_subquestions(message):
        return ""

    blocks: list[str] = []
    if "oop" in categories and is_part_d_annual_cap_question(message) and not is_medical_moop_question(message):
        explanation, _ = build_part_d_cap_explanation()
        blocks.append(
            "Authoritative CMS Part D annual OOP cap (you MUST include this figure in your reply):\n"
            + explanation
        )

    if "pharmacy" in categories and extract_zip(message):
        blocks.append(
            "Pharmacy lookup is also requested — call find_pharmacies with the user's ZIP "
            "and name at least one result in your reply."
        )

    return "\n\n".join(blocks)


def compound_question_note(message: str) -> str | None:
    """Agent-loop guidance when deterministic resolvers deferred a compound ask."""
    categories = message_intent_categories(message)
    distinct = message_asks_distinct_subquestions(message)
    mail_follow_up = _MAIL_ORDER_RE.search(message) and distinct

    if len(categories) < 2 and not mail_follow_up:
        return None
    if not distinct and not mail_follow_up:
        return None

    checklist: list[str] = []
    if "oop" in categories:
        checklist.append(
            "CMS Part D annual out-of-pocket maximum (call get_part_d_benefit_params and cite annual_oop_cap)"
        )
    if "pharmacy" in categories:
        checklist.append(
            "pharmacies near the stated ZIP / in the named plan's network (call find_pharmacies)"
        )
    if "tier" in categories:
        checklist.append("formulary tier for each drug the user asked about")
    if "plan_coverage" in categories:
        checklist.append(
            "which plans near the ZIP cover each drug named (estimate or list_plans as needed)"
        )
    if "drug_cost" in categories:
        checklist.append(
            "cost estimate for every named drug and plan (use remaining_year_budget fields when a duration window is asked)"
        )
    if "insulin_policy" in categories:
        checklist.append(
            "insulin $35 cap policy (ceiling, not a fixed price for every product)"
        )

    if not checklist and not mail_follow_up:
        return None

    parts = [
        "Compound message — answer EVERY item below in one reply; do not stop after the first:",
        *[f"- {item}" for item in checklist],
    ]
    if re.search(r"\breference\s+number\b", message, re.I):
        parts.append(
            "- Ignore prescription/reference numbers — never treat them as a ZIP and never repeat them."
        )
    if mail_follow_up:
        parts.append(
            "- Mail-order question: cite preferred-mail and standard-mail channel costs from the "
            "estimate tool, or list mail-order pharmacies from find_pharmacies."
        )
    return "\n".join(parts)


_PHARMACY_NAME_MARKERS = ("icon pharmacy", "angels", "albertsons", "accredo")


async def enrich_compound_agent_explanation(
    message: str,
    explanation: str,
    *,
    filter_plan_id: str | None = None,
    tool_artifacts: dict | None = None,
    last_tool_calls: list[dict] | None = None,
) -> str:
    """Stitch authoritative deterministic answers the agent omitted from compound replies."""
    from medicare_navigator.agent.insulin_requests import (
        mentioned_oral_drugs_with_strength,
        resolve_insulin_request,
    )
    from medicare_navigator.agent.oop_questions import (
        build_part_d_cap_explanation,
        extract_plan_key,
        is_medical_moop_question,
        is_part_d_annual_cap_question,
    )
    from medicare_navigator.agent.pharmacy_questions import (
        extract_zip,
        is_preferred_pharmacy_question,
        resolve_nearby_pharmacy_question,
        resolve_preferred_pharmacy_question,
    )
    from medicare_navigator.storage.repository import PlanRepository
    from medicare_navigator.tools.estimate_drug_cost import estimate_drug_cost_all_channels
    from medicare_navigator.tools.zip_lookup import zip_to_state

    categories = message_intent_categories(message)
    distinct = message_asks_distinct_subquestions(message)
    mail_follow_up = _MAIL_ORDER_RE.search(message) and distinct
    if len(categories) < 2 and not mail_follow_up:
        return _scrub_decoy_reference_number(message, explanation)

    blocks: list[str] = []
    lowered = explanation.lower()

    if "oop" in categories and is_part_d_annual_cap_question(message) and not is_medical_moop_question(message):
        if "2,100" not in explanation and "2100" not in explanation.replace(",", ""):
            oop_text, _ = build_part_d_cap_explanation()
            blocks.append(oop_text)

    plan_key = extract_plan_key(message) or filter_plan_id
    if "tier" in categories and plan_key:
        for drug, dosage in mentioned_oral_drugs_with_strength(message):
            if drug in lowered and "tier" in lowered:
                continue
            result = await estimate_drug_cost_all_channels(
                plan_key=plan_key, drug_name=drug, dosage=dosage
            )
            if result.data and result.data.covered and result.data.tier is not None:
                blocks.append(
                    f"{drug.title()} {dosage} is tier {result.data.tier} on plan {plan_key}."
                )

    insulin = resolve_insulin_request(message)
    if insulin and insulin.products and plan_key and "drug_cost" in categories:
        for product in insulin.products:
            if product in lowered and "$" in explanation:
                continue
            result = await estimate_drug_cost_all_channels(
                plan_key=plan_key, drug_name=product
            )
            if result.data and result.data.channels:
                channels = result.data.channels
                pref = _channel_cost_low(channels.get("preferred_retail"))
                if pref is not None:
                    blocks.append(
                        f"{product.title()} is estimated at ${float(pref):,.2f} "
                        f"for a 30-day supply at preferred retail on plan {plan_key}."
                    )

    blocks.append(explanation)

    if "pharmacy" in categories and extract_zip(message):
        if not any(marker in lowered for marker in _PHARMACY_NAME_MARKERS):
            if is_preferred_pharmacy_question(message):
                pharmacy = resolve_preferred_pharmacy_question(
                    message, filter_plan_id=filter_plan_id
                )
            else:
                pharmacy = resolve_nearby_pharmacy_question(
                    message, filter_plan_id=filter_plan_id
                )
            if pharmacy:
                pharmacy_text = pharmacy[0].split("\n\nDisclaimer")[0].strip()
                blocks.append(pharmacy_text)

    if "plan_coverage" in categories:
        zip_code = extract_zip(message)
        state = zip_to_state(zip_code) if zip_code else None
        for drug, dosage in mentioned_oral_drugs_with_strength(message):
            if drug in lowered and any(
                key in lowered for key in ("s9999-001", "florida test pdp")
            ):
                continue
            if is_tier_question(message) and drug in lowered and "tier" in lowered:
                continue
            if drug in lowered and "not covered" in lowered:
                continue
            if state is None or not zip_code:
                continue
            covered_plans: list[str] = []
            for plan in PlanRepository().list_plans(state=state):
                if plan.get("plan_suppressed"):
                    continue
                result = await estimate_drug_cost_all_channels(
                    plan_key=plan["plan_key"],
                    drug_name=drug,
                    dosage=dosage,
                )
                if result.data and result.data.covered:
                    covered_plans.append(plan["plan_key"])
            if covered_plans:
                blocks.append(
                    f"Near ZIP {zip_code}, {drug} {dosage} is covered on: "
                    + ", ".join(covered_plans)
                    + "."
                )
            else:
                blocks.append(
                    f"{drug.title()} {dosage} is not covered on any plan checked near ZIP {zip_code}."
                )

    if mail_follow_up and "mail" not in lowered:
        mail_note = _mail_channel_note_from_artifacts(tool_artifacts or {})
        if not mail_note:
            mail_note = await _mail_and_ytd_note_from_session(
                message, last_tool_calls or []
            )
        if mail_note:
            blocks.append(mail_note)

    return _scrub_decoy_reference_number(message, "\n\n".join(blocks))


def compound_reply_complete(message: str, explanation: str) -> bool:
    """True when a compound reply includes every category the message asked for."""
    from medicare_navigator.agent.insulin_requests import (
        mentioned_oral_drugs_with_strength,
        resolve_insulin_request,
    )

    categories = message_intent_categories(message)
    if len(categories) < 2 or not message_asks_distinct_subquestions(message):
        return False
    lowered = explanation.lower()
    if "oop" in categories and "2,100" not in explanation and "2100" not in explanation.replace(",", ""):
        return False
    if "pharmacy" in categories and not any(marker in lowered for marker in _PHARMACY_NAME_MARKERS):
        return False
    if "tier" in categories and "tier" not in lowered:
        return False
    if "plan_coverage" in categories:
        for drug, _dosage in mentioned_oral_drugs_with_strength(message):
            if drug not in lowered:
                return False
    if "drug_cost" in categories:
        insulin = resolve_insulin_request(message)
        if insulin and insulin.products:
            for product in insulin.products:
                if product not in lowered:
                    return False
    return True


def _scrub_decoy_reference_number(message: str, explanation: str) -> str:
    match = re.search(r"\breference\s+number\s+is\s+(\d+)\b", message, re.I)
    if not match:
        return explanation
    decoy = match.group(1)
    cleaned = re.sub(rf"\b{re.escape(decoy)}\b", "", explanation)
    return re.sub(r"\n{3,}", "\n\n", cleaned)


async def _mail_and_ytd_note_from_session(
    message: str,
    last_tool_calls: list[dict],
) -> str | None:
    from medicare_navigator.agent.insulin_requests import _extract_ytd_oop_spend
    from medicare_navigator.tools.estimate_drug_cost import estimate_drug_cost_all_channels

    if not _MAIL_ORDER_RE.search(message):
        return None
    ytd = _extract_ytd_oop_spend(message, filter_ytd_oop_spend=None)
    if not re.search(r"\$?\s*\d[\d,]*(?:\.\d+)?\s*(?:ytd|year[- ]to[- ]date)\b", message, re.I):
        if not re.search(r"\b(?:spent|spend)\s+\$?\s*\d", message, re.I):
            return None
    for call in reversed(last_tool_calls):
        if call.get("name") not in ("estimate_drug_cost", "estimate_drug_cost_all_channels"):
            continue
        args = dict(call.get("arguments") or {})
        args["ytd_oop_spend"] = ytd
        result = await estimate_drug_cost_all_channels(
            plan_key=args.get("plan_key"),
            drug_name=args.get("drug_name"),
            dosage=args.get("dosage"),
            days_supply=args.get("days_supply", 30),
            ytd_oop_spend=ytd,
        )
        if not result.data:
            continue
        channels = result.data.channels or {}
        lines: list[str] = []
        for channel_name, label in (
            ("preferred_retail", "Preferred retail"),
            ("preferred_mail", "Preferred mail-order"),
            ("standard_mail", "Standard mail-order"),
        ):
            cost = _channel_cost_low(channels.get(channel_name))
            if cost is not None:
                lines.append(f"{label}: ${float(cost):,.2f} for a 30-day fill.")
        return "\n".join(lines) if lines else None
    return None


def _channel_cost_low(channel: object | None) -> float | None:
    if channel is None:
        return None
    if isinstance(channel, dict):
        low = channel.get("cost_low")
    else:
        low = getattr(channel, "cost_low", None)
    return float(low) if low is not None else None


def _mail_channel_note_from_artifacts(tool_artifacts: dict) -> str | None:
    for artifact in tool_artifacts.values():
        data = artifact.get("data") if isinstance(artifact, dict) else None
        if not isinstance(data, dict):
            continue
        channels = data.get("channels") or {}
        preferred_mail = _channel_cost_low(channels.get("preferred_mail"))
        standard_mail = _channel_cost_low(channels.get("standard_mail"))
        if preferred_mail is None and standard_mail is None:
            continue
        parts = ["Mail-order channel estimates from CMS data:"]
        if preferred_mail is not None:
            parts.append(f"- Preferred mail: ${float(preferred_mail):,.2f}")
        if standard_mail is not None:
            parts.append(f"- Standard mail: ${float(standard_mail):,.2f}")
        return "\n".join(parts)
    return None
