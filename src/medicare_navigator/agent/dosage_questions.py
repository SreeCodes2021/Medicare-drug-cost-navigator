"""Early-return strength clarification before the agent guesses a dose or misreports coverage."""

from __future__ import annotations

import re

from medicare_navigator.tools.drug_lookup import COMMON_DRUGS, COMMON_DRUGS_REQUIRING_DOSAGE
from medicare_navigator.tools.normalize_drug import canonicalize_drug_name, dosage_candidates_for_drug

def _mentioned_common_drugs(message: str) -> list[str]:
    lower = message.lower()
    found: list[str] = []
    for drug in COMMON_DRUGS:
        if re.search(rf"\b{re.escape(drug)}\b", lower):
            found.append(drug)
    return found


def _drug_has_strength_in_message(message: str, drug: str) -> bool:
    escaped = re.escape(drug)
    return bool(
        re.search(rf"\b{escaped}\b[^\n.,;]{{0,24}}\d+\s*mg\b", message, re.I)
        or re.search(rf"\b\d+\s*mg\b[^\n.,;]{{0,24}}\b{escaped}\b", message, re.I)
    )


def drugs_missing_dosage(
    message: str,
    *,
    filter_drug: str | None = None,
    filter_dosage: str | None = None,
) -> list[str]:
    """Return common oral drugs named in the message that still need an explicit strength."""
    mentioned = _mentioned_common_drugs(message)
    if not mentioned:
        return []

    missing: list[str] = []
    filter_drug_key = canonicalize_drug_name(filter_drug) if filter_drug else None
    filter_satisfies_single = bool(
        filter_dosage and filter_dosage.strip() and len(mentioned) == 1
    )

    for drug in mentioned:
        if _drug_has_strength_in_message(message, drug):
            continue
        if filter_satisfies_single and (
            filter_drug_key is None or filter_drug_key == drug
        ):
            continue
        if drug == "januvia" and len(mentioned) == 1:
            continue
        if drug in COMMON_DRUGS_REQUIRING_DOSAGE or len(mentioned) > 1:
            missing.append(drug)
    return missing


async def build_dosage_clarification_explanation(drugs: list[str]) -> str:
    if len(drugs) == 1:
        drug = drugs[0]
        options = await dosage_candidates_for_drug(drug)
        if options:
            strengths = ", ".join(f"**{s}**" for s in options)
            return (
                f"Please specify the strength for **{drug}** — common options include {strengths}. "
                "I need the strength before I can estimate cost or benefit phase."
            )
        return (
            f"Please specify the strength (dosage) for **{drug}** before I can estimate cost "
            "or benefit phase."
        )

    lines = [
        "Please specify the strength for each drug before I can compare costs:"
    ]
    for drug in drugs:
        options = await dosage_candidates_for_drug(drug)
        if options:
            strengths = ", ".join(f"**{s}**" for s in options)
            lines.append(f"- **{drug}**: {strengths}")
        else:
            lines.append(f"- **{drug}**: include mg strength (e.g. 500 mg)")
    return "\n".join(lines)


def should_clarify_dosage_before_estimate(
    message: str,
    *,
    filter_drug: str | None = None,
    filter_dosage: str | None = None,
    plan_known: bool = False,
) -> bool:
    """Whether to intercept before the LLM/estimate tools.

    When the user already named a plan, single-drug strength gaps still defer to the
    estimate tool (suppressed-plan / insulin data-gap / needs_dosage). Multi-drug asks
    must clarify first so the model does not guess strengths.
    """
    missing = drugs_missing_dosage(
        message, filter_drug=filter_drug, filter_dosage=filter_dosage
    )
    if not missing:
        return False
    if not plan_known:
        return True
    if len(_mentioned_common_drugs(message)) > 1:
        return True
    from medicare_navigator.agent.insulin_requests import resolve_insulin_request

    insulin_request = resolve_insulin_request(message)
    if insulin_request and insulin_request.products:
        return True
    return False


async def resolve_dosage_question(
    message: str,
    *,
    filter_drug: str | None = None,
    filter_dosage: str | None = None,
) -> tuple[str, dict[str, object], list[str]] | None:
    """Return (explanation, tool_artifacts, tools_invoked) or None to defer to the LLM."""
    missing = drugs_missing_dosage(
        message, filter_drug=filter_drug, filter_dosage=filter_dosage
    )
    if not missing:
        return None
    explanation = await build_dosage_clarification_explanation(missing)
    from medicare_navigator.agent.insulin_requests import resolve_insulin_request

    insulin_request = resolve_insulin_request(message)
    if insulin_request and insulin_request.products:
        insulin_names = ", ".join(f"**{product}**" for product in insulin_request.products)
        explanation = (
            f"{explanation}\n\n"
            f"I also see {insulin_names} in your question — once the missing strength(s) "
            "are provided, I can estimate insulin and oral drug costs together on this plan."
        )
    return explanation, {}, []
