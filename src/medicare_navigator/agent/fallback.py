from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from medicare_navigator.guardrails.citations import apply_guardrails, build_citations_from_artifacts
from medicare_navigator.mcp.registry import call_tool
from medicare_navigator.models.query import QuerySlots


@dataclass
class ParsedMessage:
    drug: str | None = None
    dosage: str | None = None
    plan_key: str | None = None
    ytd_oop_spend: float | None = None
    ytd_provided: bool = False
    quantity: int | None = None
    wants_trend: bool = False
    wants_alternatives: bool = False
    wants_policy: bool = False


def _parse_message(message: str) -> ParsedMessage:
    text = message.lower()
    parsed = ParsedMessage()

    for name in [
        "metformin",
        "lisinopril",
        "atorvastatin",
        "omeprazole",
        "eliquis",
        "januvia",
        "lipitor",
    ]:
        if name in text:
            parsed.drug = name
            break

    if not parsed.drug:
        stop = {
            "plan",
            "spent",
            "show",
            "what",
            "tier",
            "copay",
            "alternatives",
            "alternative",
            "cost",
            "the",
            "for",
            "and",
            "only",
            "find",
            "many",
            "that",
            "have",
            "you",
            "did",
            "want",
            "help",
            "with",
            "buy",
            "pieces",
            "year",
            "already",
            "budgeting",
        }
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", message):
            if token.lower() not in stop:
                parsed.drug = token.lower()
                break

    dose_match = re.search(r"(\d+)\s*mg", text)
    if dose_match:
        parsed.dosage = f"{dose_match.group(1)}mg"

    plan_match = re.search(r"plan\s+([A-Za-z0-9]+-\d{3})", message, re.I)
    if not plan_match:
        plan_match = re.search(r"\b([A-Za-z]\d{4}-\d{3})\b", message, re.I)
    if plan_match:
        parsed.plan_key = plan_match.group(1).upper()

    for pattern in [
        r"spent\s+\$?\s*(\d+(?:\.\d+)?)",
        r"\$(\d+(?:\.\d+)?)\s+ytd",
        r"spent\s+(\d+(?:\.\d+)?)",
    ]:
        spend_match = re.search(pattern, text)
        if spend_match:
            parsed.ytd_oop_spend = float(spend_match.group(1))
            parsed.ytd_provided = True
            break

    qty_match = re.search(r"(\d+)\s*(?:pieces|tablets|pills|units)", text)
    if qty_match:
        parsed.quantity = int(qty_match.group(1))

    parsed.wants_trend = any(
        k in text for k in ("trend", "went up", "go up", "increase", "change", "why")
    )
    parsed.wants_alternatives = "alternative" in text
    parsed.wants_policy = "explain" in text and "cost" in text

    return parsed


def _merge_slots(parsed: ParsedMessage, filters: QuerySlots | None) -> ParsedMessage:
    if not filters:
        return parsed
    if filters.drug:
        parsed.drug = filters.drug
    if filters.dosage:
        parsed.dosage = filters.dosage
    if filters.plan_id:
        parsed.plan_key = filters.plan_id
    if filters.ytd_oop_spend is not None and filters.ytd_oop_spend > 0:
        parsed.ytd_oop_spend = filters.ytd_oop_spend
        parsed.ytd_provided = True
    return parsed


def _format_supply_section(form_data: dict) -> list[str]:
    supply = (form_data or {}).get("supply_estimate")
    if not supply:
        return []

    lines: list[str] = []
    if supply.get("scenarios"):
        lines.append("Supply cost scenarios from formulary data:")
        for scenario in supply["scenarios"]:
            lines.append(
                f"- {scenario['label']}: {scenario['formula_description']} "
                f"(estimated ${scenario['estimated_patient_cost']:.2f})"
            )
    elif supply.get("estimated_patient_cost") is not None:
        lines.append(
            f"Estimated supply cost: ${supply['estimated_patient_cost']:.2f} "
            f"({supply.get('formula_description', '')})."
        )
    for assumption in supply.get("assumptions") or []:
        lines.append(f"Assumption: {assumption}")
    return lines


async def run_fallback_navigator(
    message: str,
    filter_slots: QuerySlots | None = None,
    chat_history: list[dict] | None = None,
) -> tuple[str, dict[str, dict[str, Any]], list[str]]:
    parsed = _merge_slots(_parse_message(message), filter_slots)
    tool_artifacts: dict[str, dict[str, Any]] = {}
    tools_invoked: list[str] = []

    if not parsed.drug:
        return (
            "Which drug would you like help with? I can look up formulary tier, "
            "cost-sharing, and spending trends once you name the medication.",
            tool_artifacts,
            tools_invoked,
        )

    norm = await call_tool(
        "normalize_drug", {"drug_name": parsed.drug, "dosage": parsed.dosage}
    )
    tool_artifacts["normalize_drug"] = norm
    tools_invoked.append("normalize_drug")

    if norm.get("status") != "ok" or not norm.get("data"):
        return (
            f"I could not find a match for '{parsed.drug}'. "
            "Please check the spelling or try a different drug name.",
            tool_artifacts,
            tools_invoked,
        )

    selected = norm["data"].get("selected") or norm["data"]["candidates"][0]
    rxcui = selected.get("rxcui")
    ndc = selected.get("ndc")
    drug_name = selected.get("drug_name", parsed.drug)

    if parsed.plan_key:
        plan_result = await call_tool("lookup_plan", {"plan_key": parsed.plan_key})
        tool_artifacts["lookup_plan"] = plan_result
        tools_invoked.append("lookup_plan")

    if not parsed.plan_key and not parsed.wants_alternatives:
        return (
            f"I found {drug_name}. Which Medicare plan should I check "
            "(for example, plan S5678-012)?",
            tool_artifacts,
            tools_invoked,
        )

    if parsed.wants_alternatives and rxcui and not parsed.plan_key:
        alts = await call_tool("alternatives_finder", {"rxcui": rxcui})
        tool_artifacts["alternatives_finder"] = alts
        tools_invoked.append("alternatives_finder")
        if alts.get("status") == "ok" and alts.get("data"):
            names = ", ".join(a["drug_name"] for a in alts["data"][:5])
            explanation = (
                f"Therapeutically equivalent alternatives to {drug_name} include: {names}."
            )
            citations = build_citations_from_artifacts(tool_artifacts)
            explanation, citations, _ = apply_guardrails(explanation, tool_artifacts, citations)
            return explanation, tool_artifacts, tools_invoked

    if not parsed.plan_key:
        return (
            f"I found {drug_name}. Which Medicare plan should I check "
            "(for example, plan S5678-012)?",
            tool_artifacts,
            tools_invoked,
        )

    form_args: dict[str, Any] = {
        "plan_key": parsed.plan_key,
        "ndc": ndc,
        "ytd_oop_spend": parsed.ytd_oop_spend or 0.0,
        "ytd_oop_spend_provided": parsed.ytd_provided,
    }
    if parsed.quantity is not None:
        form_args["quantity"] = parsed.quantity

    form = await call_tool("formulary_benefit_lookup", form_args)
    tool_artifacts["formulary_benefit_lookup"] = form
    tools_invoked.append("formulary_benefit_lookup")

    if parsed.wants_trend and rxcui:
        trend = await call_tool("cost_trend_lookup", {"rxcui": rxcui})
        tool_artifacts["cost_trend_lookup"] = trend
        tools_invoked.append("cost_trend_lookup")

    if parsed.wants_alternatives and rxcui:
        alts = await call_tool("alternatives_finder", {"rxcui": rxcui})
        tool_artifacts["alternatives_finder"] = alts
        tools_invoked.append("alternatives_finder")

    parts: list[str] = []
    form_data = form.get("data") or {}

    if form.get("status") == "not_covered":
        parts.append(
            f"{drug_name} does not appear on the formulary for plan {parsed.plan_key}. "
            "Benefit phase and cost-sharing do not apply because the plan does not cover this drug."
        )
    elif form.get("status") == "ok" and form_data:
        cs = form_data.get("cost_share") or {}
        copay = cs.get("copay")
        tier = form_data.get("tier")
        phase = (form_data.get("benefit_phase") or "").replace("_", " ")
        if parsed.ytd_provided:
            parts.append(
                f"With ${parsed.ytd_oop_spend:.2f} in year-to-date out-of-pocket spending, "
                f"you are in the {phase} phase on plan {parsed.plan_key}."
            )
        if tier is not None and copay is not None:
            parts.append(
                f"{drug_name.capitalize()} is tier {tier} with a ${copay:.2f} copay per fill "
                f"(as of {form.get('as_of_date', 'the latest available date')})."
            )
        parts.extend(_format_supply_section(form_data))

    trend = tool_artifacts.get("cost_trend_lookup")
    if trend and trend.get("status") == "ok" and trend.get("data"):
        points = trend["data"]
        if len(points) >= 2:
            first, last = points[0], points[-1]
            if last.get("avg_unit_cost") and first.get("avg_unit_cost"):
                pct = (
                    (last["avg_unit_cost"] - first["avg_unit_cost"])
                    / first["avg_unit_cost"]
                    * 100
                )
                direction = "rose" if pct > 0 else "fell"
                parts.append(
                    f"Program average unit cost for {drug_name} {direction} about "
                    f"{abs(pct):.0f}% from {first['year']} to {last['year']}."
                )

    alts = tool_artifacts.get("alternatives_finder")
    if alts and alts.get("status") == "ok" and alts.get("data"):
        names = ", ".join(a["drug_name"] for a in alts["data"][:3])
        parts.append(f"Therapeutically equivalent alternatives include: {names}.")

    if not parts:
        parts.append("I retrieved data for your query but could not build a supported summary.")

    explanation = " ".join(parts)
    citations = build_citations_from_artifacts(tool_artifacts)
    explanation, citations, _ = apply_guardrails(explanation, tool_artifacts, citations)
    return explanation, tool_artifacts, tools_invoked
