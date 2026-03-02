from __future__ import annotations

import re

from pydantic import BaseModel, Field

from medicare_navigator.llm.client import llm_client
from medicare_navigator.models.query import ParsedQuery
from medicare_navigator.models.response import FormularyResult
from medicare_navigator.models.tool_result import ToolResult
from medicare_navigator.tools.ira_drugs import is_ira_negotiated
from medicare_navigator.tools.policy_retrieval import policy_retrieval

POLICY_SYSTEM_PROMPT = """You are the Policy agent for a Medicare drug cost navigator.
Interpret policy passages to explain WHY costs change (tier moves, benefit phases, IRA negotiation).
Each claim MUST reference a source_id from the provided passages.
Never recommend switching plans. Never give medical advice.

Rules:
- Do NOT claim a formulary tier change unless tier_change_evidence is provided in the structured data.
- Do NOT mention IRA drug price negotiation unless ira_negotiated is true for this drug.
- When ytd_oop_spend_assumed is true, describe benefit phase as an estimate, not a known fact."""

_TIER_CHANGE_RE = re.compile(
    r"tier change|formulary tier|moved to a (?:higher|lower) tier|tier move",
    re.I,
)
_IRA_RE = re.compile(r"\bira\b|negotiat|maximum fair price", re.I)


class PolicyClaim(BaseModel):
    claim: str
    source_id: str


class PolicyLLMOutput(BaseModel):
    claims: list[PolicyClaim] = Field(default_factory=list)


def _formulary_summary(tool_artifacts: dict[str, ToolResult]) -> str:
    form_result = tool_artifacts.get("formulary_benefit_lookup")
    if not form_result or not form_result.data:
        return "No formulary data available."
    f: FormularyResult = form_result.data
    if not f.covered:
        return f"Drug not covered on {f.plan_key}."
    return (
        f"covered=True tier={f.tier} benefit_phase={f.benefit_phase} "
        f"deductible={f.deductible} ytd_oop_spend={f.ytd_oop_spend} "
        f"ytd_oop_spend_assumed={f.ytd_oop_spend_assumed} tier_change_evidence=none"
    )


def filter_policy_claims(
    claims: list[dict],
    drug_name: str,
    tool_artifacts: dict[str, ToolResult],
) -> list[dict]:
    """Drop policy claims that contradict retrieved formulary or drug facts."""
    ira_applicable = is_ira_negotiated(drug_name)
    filtered: list[dict] = []
    for claim in claims:
        text = claim.get("claim", "")
        if _IRA_RE.search(text) and not ira_applicable:
            continue
        if _TIER_CHANGE_RE.search(text):
            continue
        filtered.append(claim)
    return filtered


async def run_policy_agent(
    parsed_query: ParsedQuery,
    tool_artifacts: dict[str, ToolResult],
) -> tuple[PolicyLLMOutput, ToolResult]:
    query_text = (
        f"Explain cost factors for {parsed_query.drug_name} "
        f"intents={parsed_query.intents} message={parsed_query.raw_message}"
    )
    retrieval = policy_retrieval(query_text)
    passages_text = ""
    if retrieval.status.value == "ok" and retrieval.data:
        passages_text = "\n\n".join(
            f"[{p['passage_id']}] {p['text']}" for p in retrieval.data
        )

    user_prompt = f"""Query: {parsed_query.raw_message}
Drug: {parsed_query.drug_name}
ira_negotiated: {is_ira_negotiated(parsed_query.drug_name)}
Formulary summary: {_formulary_summary(tool_artifacts)}
Tool summaries: {list(tool_artifacts.keys())}
Policy passages:
{passages_text or 'None found'}"""

    output = await llm_client.structured_completion(
        system_prompt=POLICY_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_model=PolicyLLMOutput,
        agent_name="policy",
    )
    filtered = filter_policy_claims(
        [c.model_dump() for c in output.claims],
        parsed_query.drug_name,
        tool_artifacts,
    )
    output.claims = [PolicyClaim.model_validate(c) for c in filtered]
    return output, retrieval
