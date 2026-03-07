from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field

from medicare_navigator.config import settings
from medicare_navigator.guardrails.source_catalog import (
    formulary_citation_claim,
    label_for_source_id,
    trend_citation_claim,
    url_for_source_id,
)
from medicare_navigator.llm.client import llm_client
from medicare_navigator.models.citation import Citation
from medicare_navigator.models.query import ParsedQuery
from medicare_navigator.models.response import CostTrendPoint, FormularyResult
from medicare_navigator.models.tool_result import ToolResult
from medicare_navigator.tools.ira_drugs import is_ira_negotiated

SYNTHESIS_SYSTEM_PROMPT = """You are the Synthesis agent for a Medicare drug cost navigator.
Write a plain-language explanation using ONLY the structured data provided.
Answer the user's latest question directly in the first sentence. If they ask about prior
results (count, completeness, confirmation), respond to that first.
Use short conversational paragraphs — no emoji, no markdown headers, no horizontal rules,
and no numbered "key reasons" lists unless the user asks for a detailed breakdown.
Keep most answers to 3–6 sentences.
Every factual claim must have a citation with source_id from the provided data.
Never recommend switching plans. Never give medical advice.
When a drug is NOT covered on the formulary, do NOT mention benefit phase, deductible
phase, YTD spending, or out-of-pocket thresholds — those do not apply to non-covered drugs.
When ytd_oop_spend_assumed is true and the drug IS covered, state that assumption in the
first sentence before drawing any conclusions about benefit phase.
Do NOT mention formulary tier changes unless tier_change_evidence is provided in the data.
Do NOT mention Medicare Part D formulary, coverage, or tier unless formulary_data_available
is true in the structured data. For alternatives-only answers, stick to FDA Orange Book therapeutic equivalents and note that
plan-specific cost/coverage was not checked. Do not generalize about generic vs brand pricing
or availability unless cost_trend_lookup or formulary_benefit_lookup data supports it.
Do NOT mention IRA drug price negotiation unless ira_negotiated is true for the drug.
When stating facts from tool data, include that tool's as_of_date in the body
(e.g. "as of January 15, 2026").
The first time you use Medicare terms like "formulary" or "tier", add a brief plain-language
gloss (formulary = your plan's covered-drug list; tier = the cost level your plan assigns).
Note early that figures are from government data, not real-time pharmacy pricing.
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


def _format_as_of(iso_date: str) -> str:
    if not iso_date:
        return "the latest available date"
    try:
        d = date.fromisoformat(iso_date)
    except ValueError:
        return iso_date
    return f"{d.strftime('%B')} {d.day}, {d.year}"


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
        f"ira_negotiated: {is_ira_negotiated(parsed_query.drug_name)}",
        f"tier_change_evidence: none",
        f"YTD OOP spend provided by user: {parsed_query.ytd_oop_spend_provided}",
    ]
    if parsed_query.ytd_oop_spend_provided:
        lines.append(f"YTD OOP spend: ${parsed_query.ytd_oop_spend:.2f}")
    else:
        lines.append(
            "YTD OOP spend: not provided (if drug is covered, phase estimates assume $0 YTD spend)"
        )
    form_result = tool_artifacts.get("formulary_benefit_lookup")
    has_formulary = bool(
        form_result
        and form_result.status.value in ("ok", "not_covered")
        and form_result.data
    )
    lines.append(f"formulary_data_available: {has_formulary}")
    for name, result in tool_artifacts.items():
        lines.append(
            f"Tool {name}: status={result.status.value} source={result.source_id} "
            f"as_of_date={result.as_of_date}"
        )
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
    as_of = _format_as_of(alt_result.as_of_date)
    if count == 1:
        alt = alts[0]
        te = f" (TE code {alt.te_code})" if alt.te_code else ""
        explanation = (
            f"Yes. As of {as_of}, we found one therapeutically equivalent alternative for "
            f"{parsed_query.drug_name} in the FDA Orange Book demo data: {alt.drug_name}{te}."
        )
    else:
        names = ", ".join(
            f"{a.drug_name} (TE {a.te_code})" if a.te_code else a.drug_name for a in alts
        )
        explanation = (
            f"Yes. As of {as_of}, we found {count} therapeutically equivalent alternatives for "
            f"{parsed_query.drug_name} in the FDA Orange Book demo data: {names}."
        )

    citations = [
        Citation(
            claim=f"{count} therapeutic alternative(s) for {parsed_query.drug_name}",
            source_id=alt_result.source_id,
            as_of_date=alt_result.as_of_date,
            source_label=label_for_source_id(alt_result.source_id),
            url=url_for_source_id(alt_result.source_id),
        )
    ]
    return explanation, citations


def _is_cost_change_query(parsed_query: ParsedQuery) -> bool:
    return "explain_cost_change" in (parsed_query.intents or [])


def _trend_context_sentence(drug_name: str, points: list[CostTrendPoint]) -> str | None:
    if len(points) < 2:
        return None
    first, last = points[0], points[-1]
    if first.avg_unit_cost is None or last.avg_unit_cost is None:
        return None
    if first.avg_unit_cost <= 0:
        return None
    pct = round((last.avg_unit_cost - first.avg_unit_cost) / first.avg_unit_cost * 100)
    return (
        f"National average unit cost for {drug_name} rose from ${first.avg_unit_cost:.2f} "
        f"({first.year}) to ${last.avg_unit_cost:.2f} ({last.year}) — about {pct}% — "
        f"but that is usually a smaller factor than benefit-phase pricing."
    )


def _explain_cost_change_answer(
    parsed_query: ParsedQuery,
    tool_artifacts: dict[str, ToolResult],
) -> tuple[str, list[Citation]] | None:
    if not _is_cost_change_query(parsed_query):
        return None

    form_result = tool_artifacts.get("formulary_benefit_lookup")
    if not form_result or form_result.status.value != "ok" or not form_result.data:
        return None

    formulary: FormularyResult = form_result.data
    if not formulary.covered:
        return None

    citations: list[Citation] = [
        Citation(
            claim=formulary_citation_claim(formulary.model_dump(), parsed_query.drug_name),
            source_id=form_result.source_id,
            as_of_date=form_result.as_of_date,
            source_label=label_for_source_id(form_result.source_id),
            url=url_for_source_id(form_result.source_id),
        )
    ]

    drug = parsed_query.drug_name
    copay = formulary.cost_share.copay if formulary.cost_share else None
    copay_str = f"${copay:.2f}" if copay is not None else "the plan copay"
    tier = formulary.tier
    phase = formulary.benefit_phase or ""
    sentences: list[str] = []

    if formulary.ytd_oop_spend_assumed:
        opener = (
            "I don't have your year-to-date out-of-pocket spending, so I'm assuming $0. "
        )
    else:
        opener = ""

    if phase == "deductible":
        if formulary.ytd_oop_spend_assumed:
            sentences.append(
                f"{opener}Most likely you're paying full price because you haven't met your "
                f"${formulary.deductible:.0f} deductible yet on {formulary.plan_name} "
                f"({formulary.plan_key}), not because {drug} got more expensive — once you do, "
                f"it should be a {copay_str} Tier {tier} copay."
            )
        else:
            sentences.append(
                f"With ${formulary.ytd_oop_spend:.2f} in year-to-date out-of-pocket spending, "
                f"you're in the deductible phase on {formulary.plan_name} ({formulary.plan_key}), "
                f"so you likely pay the full drug cost rather than the {copay_str} Tier {tier} copay."
            )
    elif phase == "initial_coverage":
        if formulary.ytd_oop_spend_assumed:
            sentences.append(
                f"{opener}On {formulary.plan_name} ({formulary.plan_key}), {drug} is Tier {tier} "
                f"with a {copay_str} copay during initial coverage."
            )
        else:
            sentences.append(
                f"With ${formulary.ytd_oop_spend:.2f} in year-to-date out-of-pocket spending, "
                f"you're in initial coverage on {formulary.plan_name} ({formulary.plan_key}), "
                f"where {drug} is Tier {tier} with a {copay_str} copay."
            )
    elif phase == "catastrophic":
        sentences.append(
            f"On {formulary.plan_name} ({formulary.plan_key}), you've reached catastrophic "
            f"coverage with ${formulary.ytd_oop_spend:.2f} in year-to-date out-of-pocket spending, "
            f"so {drug} should cost $0."
        )
    else:
        sentences.append(
            f"{opener}On {formulary.plan_name} ({formulary.plan_key}), {drug} is Tier {tier} "
            f"with a {copay_str} copay."
        )

    sentences.append(
        f"{drug.capitalize()} is Tier {tier} on this plan; we have no prior-year tier data "
        f"for this drug on this plan."
    )

    trend_result = tool_artifacts.get("cost_trend_lookup")
    if trend_result and trend_result.status.value == "ok" and trend_result.data:
        trend_sentence = _trend_context_sentence(drug, trend_result.data)
        if trend_sentence:
            sentences.append(trend_sentence)
            points_dump = [p.model_dump() for p in trend_result.data]
            citations.append(
                Citation(
                    claim=trend_citation_claim(points_dump, parsed_query.drug_name),
                    source_id=trend_result.source_id,
                    as_of_date=trend_result.as_of_date,
                    source_label=label_for_source_id(trend_result.source_id),
                    url=url_for_source_id(trend_result.source_id),
                )
            )

    if formulary.ytd_oop_spend_assumed:
        sentences.append(
            "Share your year-to-date Part D out-of-pocket spending for a more precise answer."
        )

    return " ".join(sentences), citations


def _phase_phrase(formulary: FormularyResult, ytd_provided: bool) -> str:
    phase_label = formulary.benefit_phase.replace("_", " ") if formulary.benefit_phase else ""
    if ytd_provided:
        return (
            f"You are currently in the {phase_label} phase "
            f"with ${formulary.ytd_oop_spend:.2f} in year-to-date out-of-pocket spending."
        )
    return (
        f"Benefit phase is estimated as {phase_label} assuming $0.00 in year-to-date "
        f"out-of-pocket spending (you did not provide your YTD spend)."
    )


def _deterministic_explanation(
    parsed_query: ParsedQuery,
    tool_artifacts: dict[str, ToolResult],
    policy_claims: list[dict] | None,
) -> tuple[str, list[Citation]]:
    follow_up = _follow_up_alternatives_answer(parsed_query, tool_artifacts)
    if follow_up:
        return follow_up

    cost_change = _explain_cost_change_answer(parsed_query, tool_artifacts)
    if cost_change:
        return cost_change

    citations: list[Citation] = []
    parts: list[str] = []

    form_result = tool_artifacts.get("formulary_benefit_lookup")
    if form_result and form_result.status.value == "ok" and form_result.data:
        f: FormularyResult = form_result.data
        cs = f.cost_share
        copay_str = f"${cs.copay:.2f} copay" if cs and cs.copay is not None else "cost-sharing per plan"
        parts.append(
            f"On {f.plan_name} ({f.plan_key}), {parsed_query.drug_name} is on formulary tier {f.tier} "
            f"with {copay_str}. {_phase_phrase(f, parsed_query.ytd_oop_spend_provided)}"
        )
        citations.append(
            Citation(
                claim=formulary_citation_claim(f.model_dump(), parsed_query.drug_name),
                source_id=form_result.source_id,
                as_of_date=form_result.as_of_date,
                source_label=label_for_source_id(form_result.source_id),
                url=url_for_source_id(form_result.source_id),
            )
        )
    elif form_result and form_result.status.value == "not_covered" and form_result.data:
        f: FormularyResult = form_result.data
        parts.append(
            f"{parsed_query.drug_name} does not appear on the formulary for "
            f"{f.plan_name} ({f.plan_key}). Benefit phase, deductible, and cost-sharing "
            f"do not apply because the plan does not cover this drug."
        )
        citations.append(
            Citation(
                claim="Drug not covered on plan formulary",
                source_id=form_result.source_id,
                as_of_date=form_result.as_of_date,
                source_label=label_for_source_id(form_result.source_id),
                url=url_for_source_id(form_result.source_id),
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
            points_dump = [p.model_dump() for p in points]
            citations.append(
                Citation(
                    claim=trend_citation_claim(points_dump, parsed_query.drug_name),
                    source_id=trend_result.source_id,
                    as_of_date=trend_result.as_of_date,
                    source_label=label_for_source_id(trend_result.source_id),
                    url=url_for_source_id(trend_result.source_id),
                )
            )

    alt_result = tool_artifacts.get("alternatives_finder")
    if alt_result and alt_result.status.value == "ok" and alt_result.data:
        names = ", ".join(a.drug_name for a in alt_result.data[:3])
        as_of = _format_as_of(alt_result.as_of_date)
        parts.append(
            f"As of {as_of}, therapeutically equivalent alternatives include: {names}."
        )
        citations.append(
            Citation(
                claim=f"{parsed_query.drug_name.capitalize()} therapeutic alternatives",
                source_id=alt_result.source_id,
                as_of_date=alt_result.as_of_date,
                source_label=label_for_source_id(alt_result.source_id),
                url=url_for_source_id(alt_result.source_id),
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

    cost_change = _explain_cost_change_answer(parsed_query, tool_artifacts)
    if cost_change:
        explanation, citations = cost_change
        if settings.disclaimer_text not in explanation:
            explanation = f"{explanation}\n\n{settings.disclaimer_text}"
        return explanation, citations, "Deterministic cost-change explanation (synthesis)"

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
