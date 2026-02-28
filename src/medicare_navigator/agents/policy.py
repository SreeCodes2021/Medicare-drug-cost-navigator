from __future__ import annotations

from pydantic import BaseModel, Field

from medicare_navigator.llm.client import llm_client
from medicare_navigator.models.citation import Citation
from medicare_navigator.models.query import ParsedQuery
from medicare_navigator.models.tool_result import ToolResult
from medicare_navigator.tools.policy_retrieval import policy_retrieval

POLICY_SYSTEM_PROMPT = """You are the Policy agent for a Medicare drug cost navigator.
Interpret policy passages to explain WHY costs change (tier moves, benefit phases, IRA negotiation).
Each claim MUST reference a source_id from the provided passages.
Never recommend switching plans. Never give medical advice."""


class PolicyClaim(BaseModel):
    claim: str
    source_id: str


class PolicyLLMOutput(BaseModel):
    claims: list[PolicyClaim] = Field(default_factory=list)


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
Tool summaries: {list(tool_artifacts.keys())}
Policy passages:
{passages_text or 'None found'}"""

    output = await llm_client.structured_completion(
        system_prompt=POLICY_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_model=PolicyLLMOutput,
        agent_name="policy",
    )
    return output, retrieval
