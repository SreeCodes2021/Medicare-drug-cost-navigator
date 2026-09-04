"""Detect and answer out-of-pocket / MOOP questions without spurious plan lookups."""

from __future__ import annotations

import re
from typing import Any

from medicare_navigator.tools.lookup_plan import lookup_plan
from medicare_navigator.tools.part_d_benefit_lookup import get_part_d_benefit_params
from medicare_navigator.models.tool_result import ToolResult

_PLAN_KEY_RE = re.compile(r"[A-Za-z]\d{4}-\d{3}")

_PART_D_CAP_PATTERNS = (
    re.compile(r"\bpart\s+d\b.*\b(?:annual\s+)?(?:out[- ]of[- ]pocket|oop)\b", re.I),
    re.compile(
        r"\b(?:annual\s+)?(?:out[- ]of[- ]pocket|oop)\s+(?:maximum|cap|limit)\b.*\b(?:part\s+d|202[56])\b",
        re.I,
    ),
    re.compile(r"\bcms\s+part\s+d\s+annual\b", re.I),
)

_MEDICAL_MOOP_PATTERNS = (
    re.compile(r"\bmoop\b", re.I),
    re.compile(r"\bin[- ]network\b.*\bout[- ]of[- ]network\b", re.I),
    re.compile(r"\bout[- ]of[- ]network\b.*\bin[- ]network\b", re.I),
    re.compile(r"\bmedical\s+(?:maximum\s+)?out[- ]of[- ]pocket\b", re.I),
)

_OOP_SIGNAL_PATTERNS = (
    re.compile(r"\bmax(?:imum)?\s+(?:out[- ]of[- ]pocket|oop)\b", re.I),
    re.compile(r"\baccording to (?:the )?cms\b", re.I),
)

_ANY_PLAN_RE = re.compile(r"\b(?:for\s+)?any\s+plan\b", re.I)
_IN_OUT_NETWORK_RE = re.compile(
    r"\b(?:in[- ]network|in\s+and\s+out\s+of\s+network).{0,40}(?:out[- ]of[- ]network|in\s+and\s+out\s+of\s+network)\b",
    re.I,
)
_IN_OUT_NETWORK_SINGLE_RE = re.compile(
    r"\b(?:in\s+and\s+out\s+of\s+network|in[- ]network\s+(?:vs\.?|versus)\s+out[- ]of[- ]network)\b",
    re.I,
)
_MAX_OOP_WITH_PLAN_RE = re.compile(r"\bmax(?:imum)?\s+oop\b", re.I)


def extract_plan_key(message: str) -> str | None:
    match = _PLAN_KEY_RE.search(message)
    return match.group(0).upper() if match else None


def extract_plan_keys(message: str) -> list[re.Match[str]]:
    """Every plan-key regex match in the message, in order of appearance.

    Lets callers disambiguate which plan a message is actually asking about when more
    than one plan key is named — extract_plan_key's first-match behavior is wrong for a
    message like "compare plan A vs plan B... what's in B's network?", where the question
    is about the *second* plan named, not the first.
    """
    return list(_PLAN_KEY_RE.finditer(message))


def is_any_plan_wording(message: str) -> bool:
    return bool(_ANY_PLAN_RE.search(message))


def is_part_d_annual_cap_question(message: str) -> bool:
    return any(pattern.search(message) for pattern in _PART_D_CAP_PATTERNS)


def is_medical_moop_question(message: str) -> bool:
    if any(pattern.search(message) for pattern in _MEDICAL_MOOP_PATTERNS):
        return True
    if _IN_OUT_NETWORK_RE.search(message) or _IN_OUT_NETWORK_SINGLE_RE.search(message):
        return True
    if _MAX_OOP_WITH_PLAN_RE.search(message) and extract_plan_key(message):
        return True
    return False


def is_oop_question(message: str) -> bool:
    return (
        is_part_d_annual_cap_question(message)
        or is_medical_moop_question(message)
        or any(pattern.search(message) for pattern in _OOP_SIGNAL_PATTERNS)
    )


def _format_currency(amount: float) -> str:
    return f"${amount:,.2f}"


def _serialize_result(result: ToolResult) -> dict:
    return {
        "status": result.status.value,
        "source_id": result.source_id,
        "as_of_date": result.as_of_date,
        "message": result.message,
        "data": result.data,
    }


def build_part_d_cap_explanation(contract_year: int | None = None) -> tuple[str, dict[str, Any]]:
    result = get_part_d_benefit_params(contract_year)
    data = result.data or {}
    cap = float(data["annual_oop_cap"])
    year = int(data["contract_year"])
    explanation = (
        f"The CMS Part D annual out-of-pocket maximum for {year} is "
        f"{_format_currency(cap)} for covered Part D prescription drugs. "
        "This statutory cap applies across Part D and MA-PD drug benefits — it is not the same "
        "as a Medicare Advantage plan's medical-network maximum out-of-pocket (MOOP) limit."
    )
    return explanation, {"get_part_d_benefit_params": _serialize_result(result)}


def build_generic_oop_explanation(contract_year: int | None = None) -> tuple[str, dict[str, Any]]:
    result = get_part_d_benefit_params(contract_year)
    data = result.data or {}
    cap = float(data["annual_oop_cap"])
    year = int(data["contract_year"])
    explanation = (
        f"CMS publishes two different “maximum out-of-pocket” concepts. "
        f"For **Part D prescription drugs**, the statutory annual out-of-pocket maximum for {year} "
        f"is **{_format_currency(cap)}** — the same cap applies on any Part D or MA-PD plan's "
        f"drug benefit once your tracked Part D drug spending reaches that amount. "
        f"For **Medicare Advantage medical benefits**, each plan's in-network and "
        f"out-of-network MOOP limits are **not** in the CMS SPUF formulary data this tool uses — "
        f"check the plan's Evidence of Coverage or Summary of Benefits for those figures. "
        f"If you name a specific drug and plan, I can estimate that prescription fill's cost."
    )
    return explanation, {"get_part_d_benefit_params": _serialize_result(result)}


def build_medical_moop_refusal(plan_key: str) -> tuple[str, dict[str, Any]]:
    lookup = lookup_plan(plan_key=plan_key)
    lookup_payload = _serialize_result(lookup)
    plan_label = plan_key
    if lookup.status.value == "ok" and lookup.data:
        plan = lookup.data.get("plan") or {}
        if plan.get("plan_name"):
            plan_label = f"{plan['plan_name']} ({plan.get('plan_key', plan_key)})"

    explanation = (
        f"I looked up {plan_label}. CMS SPUF formulary data does not include the plan's medical "
        f"maximum out-of-pocket (MOOP) or separate in-network versus out-of-network MOOP limits — "
        f"those come from the plan's Evidence of Coverage or Summary of Benefits, not the Part D "
        f"formulary files used here. If you name a specific prescription and strength, I can "
        f"estimate that fill's Part D pharmacy cost on this plan."
    )
    return explanation, {"lookup_plan": lookup_payload}


def resolve_oop_question(
    message: str,
    *,
    filter_plan_id: str | None = None,
    contract_year: int | None = None,
) -> tuple[str, dict[str, Any], list[str]] | None:
    """Return (explanation, tool_artifacts, tools_invoked) or None to defer to the LLM."""
    if not is_oop_question(message):
        return None

    if is_part_d_annual_cap_question(message) and not is_medical_moop_question(message):
        explanation, artifacts = build_part_d_cap_explanation(contract_year)
        return explanation, artifacts, ["get_part_d_benefit_params"]

    plan_key = extract_plan_key(message)
    if plan_key and is_medical_moop_question(message):
        explanation, artifacts = build_medical_moop_refusal(plan_key)
        return explanation, artifacts, ["lookup_plan"]

    if is_any_plan_wording(message) or not plan_key:
        if is_medical_moop_question(message) and not is_any_plan_wording(message):
            plan_key = filter_plan_id
            if plan_key:
                explanation, artifacts = build_medical_moop_refusal(plan_key)
                return explanation, artifacts, ["lookup_plan"]
        explanation, artifacts = build_generic_oop_explanation(contract_year)
        return explanation, artifacts, ["get_part_d_benefit_params"]

    if is_part_d_annual_cap_question(message):
        explanation, artifacts = build_part_d_cap_explanation(contract_year)
        return explanation, artifacts, ["get_part_d_benefit_params"]

    explanation, artifacts = build_generic_oop_explanation(contract_year)
    return explanation, artifacts, ["get_part_d_benefit_params"]
