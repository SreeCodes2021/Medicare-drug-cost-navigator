"""Deterministic tier-only lookups so tier prose matches /api/estimate's covered status.

Tier-only questions ("what tier is X on plan Y") previously fell through to the LLM
agent loop, which could guess a dosage or skip the estimate tool entirely and answer
"not covered" even when estimate_drug_cost_all_channels reports covered=True for the
same drug/plan. This resolver calls the same tool the cost-ask path uses, for any
COMMON_DRUGS name that does not require an explicit strength (today: januvia only),
so tier answers are grounded in the identical formulary lookup as cost answers.
"""

from __future__ import annotations

import re

from medicare_navigator.agent.dosage_questions import _mentioned_common_drugs
from medicare_navigator.tools.drug_lookup import COMMON_DRUGS_REQUIRING_DOSAGE
from medicare_navigator.tools.estimate_drug_cost import estimate_drug_cost_all_channels

_TIER_RE = re.compile(r"\b(?:what|which)\s+(?:formulary\s+)?tier\b|\bformulary\s+tier\b", re.I)
_PLAN_KEY_RE = re.compile(r"\b[A-Za-z]\d{4}-\d{3}\b")


def is_tier_question(message: str) -> bool:
    return bool(_TIER_RE.search(message))


def _serialize_result(result) -> dict:
    return {
        "status": result.status.value,
        "source_id": result.source_id,
        "as_of_date": result.as_of_date,
        "message": result.message,
        "data": result.data,
    }


async def resolve_tier_question(
    message: str,
    *,
    filter_plan_id: str | None = None,
    filter_drug: str | None = None,
) -> tuple[str, dict[str, object], list[str]] | None:
    """Return (explanation, tool_artifacts, tools_invoked) or None to defer to the LLM."""
    if not is_tier_question(message):
        return None

    mentioned = [
        drug for drug in _mentioned_common_drugs(message) if drug not in COMMON_DRUGS_REQUIRING_DOSAGE
    ]
    drug_name = mentioned[0] if mentioned else filter_drug
    if not drug_name:
        return None

    plan_match = _PLAN_KEY_RE.search(message)
    plan_key = plan_match.group(0).upper() if plan_match else filter_plan_id
    if not plan_key:
        return None

    result = await estimate_drug_cost_all_channels(plan_key=plan_key, drug_name=drug_name)
    artifacts = {"estimate_drug_cost_all_channels": _serialize_result(result)}
    tools_invoked = ["estimate_drug_cost_all_channels"]

    if result.status.value != "ok" or result.data is None:
        message_text = result.message or f"I couldn't find formulary tier data for {drug_name} on {plan_key}."
        return message_text, artifacts, tools_invoked

    data = result.data
    if not data.covered:
        return (
            f"{drug_name.title()} is not covered on plan {plan_key} according to CMS formulary data.",
            artifacts,
            tools_invoked,
        )

    tier = data.tier
    if tier is None and data.tiers_matched:
        tier = data.tiers_matched[0]

    if tier is None:
        return (
            f"{drug_name.title()} is covered on plan {plan_key}, but CMS formulary data doesn't "
            "publish a tier for it.",
            artifacts,
            tools_invoked,
        )

    return (
        f"{drug_name.title()} is tier {tier} on plan {plan_key}.",
        artifacts,
        tools_invoked,
    )
