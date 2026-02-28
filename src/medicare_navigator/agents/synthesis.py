from __future__ import annotations

import re

from pydantic import BaseModel, Field

from medicare_navigator.config import settings
from medicare_navigator.llm.client import llm_client
from medicare_navigator.models.citation import Citation
from medicare_navigator.models.query import ParsedQuery
from medicare_navigator.models.response import FormularyResult
from medicare_navigator.models.tool_result import ToolResult

SYNTHESIS_SYSTEM_PROMPT = """You are the Synthesis agent for a Medicare drug cost navigator.
Write a plain-language explanation using ONLY the structured data provided.
Answer the user's latest question directly. If they ask about prior results (count,
completeness, confirmation), respond to that first. Do not repeat the full prior answer
unless they ask for a recap.
Every factual claim must have a citation with source_id from the provided data.
Never recommend switching plans. Never give medical advice.
Include the disclaimer verbatim at the end."""

_META_COUNT_RE = re.compile(
    r"only one|how many alternative|did you find|just one|any other alternative|is that all",
    re.I,
)


class SynthesisCitation(BaseModel):
    claim: str
    source_id: str
    as_of_date: str = ""


class SynthesisLLMOutput(BaseModel):
    explanation: str
    citations: list[SynthesisCitation] = Field(default_factory=list)


def _format_chat_history(chat_history: list[dict] | None, max_turns: int = 3) -> str:
    if not chat_history:
        return ""
    recent = chat_history[-(max_turns * 2) :]
    lines = ["Recent conversation:"]
    for entry in recent:
        role = entry.get("role", "user").capitalize()
        content = entry.get("content", "")
        if len(content) > 400:
            content = content[:400] + "..."
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _build_context(
    parsed_query: ParsedQuery,
    tool_artifacts: dict[str, ToolResult],
    policy_claims: list[dict] | None = None,
) -> str:
    lines = [
        f"User question: {parsed_query.raw_message}",
        f"Drug: {parsed_query.drug_name}",
        f"Plan: {parsed_query.plan_key}",
    ]
    for name, result in tool_artifacts.items():
        lines.append(f"Tool {name}: status={result.status.value} source={result.source_id}")
        if name == "alternatives_finder" and result.data:
            names = [a.drug_name for a in result.data]
            lines.append(f"  count={len(result.data)}, names={names}")
        if result.data:
            lines.append(f"  data={result.data}")
    if policy_claims:
        for c in policy_claims:
            lines.append(f"Policy claim [{c.get('source_id')}]: {c.get('claim')}")
    return "\n".join(lines)


def _follow_up_alternatives_answer(
    parsed_query: ParsedQuery,
    tool_artifacts: dict[str, ToolResult],
) -> tuple[str, list[Citation]] | None:
    if not _META_COUNT_RE.search(parsed_query.raw_message):
        return None

    alt_result = tool_artifacts.get("alternatives_finder")
    if not alt_result or alt_result.status.value != "ok" or not alt_result.data:
        return None

    alts = alt_result.data
    count = len(alts)
    if count == 1:
        alt = alts[0]
        te = f" (TE code {alt.te_code})" if alt.te_code else ""
        explanation = (
            f"Yes. We found one therapeutically equivalent alternative for {parsed_query.drug_name} "
            f"in the FDA Orange Book demo data: {alt.drug_name}{te}."
        )
    else:
        names = ", ".join(
            f"{a.drug_name} (TE {a.te_code})" if a.te_code else a.drug_name for a in alts
        )
        explanation = (
            f"Yes. We found {count} therapeutically equivalent alternatives for "
            f"{parsed_query.drug_name} in the FDA Orange Book demo data: {names}."
        )

    citations = [
        Citation(
            claim=f"{count} therapeutic alternative(s) for {parsed_query.drug_name}",
            source_id=alt_result.source_id,
            as_of_date=alt_result.as_of_date,
            source_label="FDA Orange Book Demo",
        )
    ]
    return explanation, citations


def _deterministic_explanation(
    parsed_query: ParsedQuery,
    tool_artifacts: dict[str, ToolResult],
    policy_claims: list[dict] | None,
) -> tuple[str, list[Citation]]:
    follow_up = _follow_up_alternatives_answer(parsed_query, tool_artifacts)
    if follow_up:
        return follow_up

    citations: list[Citation] = []
    parts: list[str] = []

    form_result = tool_artifacts.get("formulary_benefit_lookup")
    if form_result and form_result.status.value == "ok" and form_result.data:
        f: FormularyResult = form_result.data
        cs = f.cost_share
        copay_str = f"${cs.copay:.2f} copay" if cs and cs.copay is not None else "cost-sharing per plan"
        parts.append(
            f"On {f.plan_name} ({f.plan_key}), {parsed_query.drug_name} is on formulary tier {f.tier} "
            f"with {copay_str}. You are currently in the {f.benefit_phase.replace('_', ' ')} phase "
            f"with ${f.ytd_oop_spend:.2f} in year-to-date out-of-pocket spending."
        )
        citations.append(
            Citation(
                claim=f"Tier {f.tier} cost-sharing on {f.plan_key}",
                source_id=form_result.source_id,
                as_of_date=form_result.as_of_date,
                source_label="CMS SPUF Demo Formulary",
            )
        )
    elif form_result and form_result.status.value == "not_covered":
        parts.append(
            f"{parsed_query.drug_name} does not appear on the formulary for plan {parsed_query.plan_key}."
        )
        citations.append(
            Citation(
                claim="Drug not covered on plan formulary",
                source_id=form_result.source_id,
                as_of_date=form_result.as_of_date,
            )
        )

    trend_result = tool_artifacts.get("cost_trend_lookup")
    if trend_result and trend_result.status.value == "ok" and trend_result.data:
        points = trend_result.data
        if len(points) >= 2:
            first, last = points[0], points[-1]
            direction = "increased" if last.total_spend > first.total_spend else "decreased"
            parts.append(
                f"Program spending for {parsed_query.drug_name} {direction} from "
                f"${first.total_spend:,.0f} in {first.year} to ${last.total_spend:,.0f} in {last.year}."
            )
            citations.append(
                Citation(
                    claim=f"Multi-year spending trend {first.year}-{last.year}",
                    source_id=trend_result.source_id,
                    as_of_date=trend_result.as_of_date,
                    source_label="CMS Part D Spending Demo",
                )
            )

    alt_result = tool_artifacts.get("alternatives_finder")
    if alt_result and alt_result.status.value == "ok" and alt_result.data:
        names = ", ".join(a.drug_name for a in alt_result.data[:3])
        parts.append(f"Therapeutically equivalent alternatives include: {names}.")
        citations.append(
            Citation(
                claim="Therapeutic alternatives",
                source_id=alt_result.source_id,
                as_of_date=alt_result.as_of_date,
                source_label="FDA Orange Book Demo",
            )
        )

    if policy_claims:
        for c in policy_claims:
            parts.append(c["claim"])
            citations.append(
                Citation(
                    claim=c["claim"],
                    source_id=c["source_id"],
                    as_of_date="2026-01-15",
                    source_label="CMS Policy Corpus",
                )
            )

    explanation = " ".join(parts) if parts else "No supported findings available for this query."
    return explanation, citations


async def run_synthesis_agent(
    parsed_query: ParsedQuery,
    tool_artifacts: dict[str, ToolResult],
    policy_claims: list[dict] | None = None,
    chat_history: list[dict] | None = None,
    follow_up_type: str | None = None,
) -> tuple[str, list[Citation], str]:
    follow_up = _follow_up_alternatives_answer(parsed_query, tool_artifacts)
    if follow_up or follow_up_type == "clarify_count":
        explanation, citations = follow_up or _deterministic_explanation(
            parsed_query, tool_artifacts, policy_claims
        )
        if settings.disclaimer_text not in explanation:
            explanation = f"{explanation}\n\n{settings.disclaimer_text}"
        return explanation, citations, "Deterministic follow-up (synthesis)"

    context = _build_context(parsed_query, tool_artifacts, policy_claims)
    history_block = _format_chat_history(chat_history)
    user_prompt_parts = [
        "Answer the user's latest question directly. If they ask about prior results, respond to that first.",
    ]
    if history_block:
        user_prompt_parts.append(history_block)
    user_prompt_parts.extend(
        [
            f"Structured data:\n{context}",
            f"\nDisclaimer to append:\n{settings.disclaimer_text}",
        ]
    )

    llm_out = await llm_client.structured_completion(
        system_prompt=SYNTHESIS_SYSTEM_PROMPT,
        user_prompt="\n\n".join(user_prompt_parts),
        response_model=SynthesisLLMOutput,
        agent_name="synthesis",
    )

    det_explanation, det_citations = _deterministic_explanation(
        parsed_query, tool_artifacts, policy_claims
    )

    response_source = llm_client.fallback_label("synthesis")

    if llm_client._has_credentials() and llm_out.citations:
        citations = [
            Citation(claim=c.claim, source_id=c.source_id, as_of_date=c.as_of_date or "2026-01-15")
            for c in llm_out.citations
            if any(c.source_id == art.source_id for art in tool_artifacts.values())
        ]
        explanation = llm_out.explanation
        if citations:
            response_source = llm_client.model_label()
        else:
            explanation, citations = det_explanation, det_citations
    else:
        explanation, citations = det_explanation, det_citations

    if settings.disclaimer_text not in explanation:
        explanation = f"{explanation}\n\n{settings.disclaimer_text}"

    return explanation, citations, response_source
