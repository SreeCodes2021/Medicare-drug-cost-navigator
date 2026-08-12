from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from medicare_navigator.agent.mediator import MediatorRewrite

from medicare_navigator.agent.prompts import build_navigator_system_prompt
from medicare_navigator.config import settings
from medicare_navigator.guardrails.citations import (
    apply_guardrails,
    build_citations_from_artifacts,
    channel_estimate_from_artifact,
    channel_estimates_from_artifact,
    estimate_from_artifact,
)
from medicare_navigator.llm.client import llm_client
from medicare_navigator.llm.errors import LLMRequestError
from medicare_navigator.llm.models import default_llm_model, estimate_cost_usd, resolve_model
from medicare_navigator.llm.types import TokenUsage
from medicare_navigator.mcp.registry import call_tool, serialize_tool_result, tool_result_json
from medicare_navigator.mcp.schemas import anthropic_tools, openai_tools
from medicare_navigator.models.query import QuerySlots
from medicare_navigator.models.response import (
    CombinedUsage,
    DrugCostEstimate,
    LlmUsage,
    MultiChannelDrugCostEstimate,
    QueryResponse,
)
from medicare_navigator.session.manager import session_manager
from medicare_navigator.tools.estimate_drug_cost import estimate_drug_cost_all_channels

logger = logging.getLogger(__name__)


_ESTIMATE_TOOL_NAMES = ("estimate_drug_cost", "estimate_drug_cost_all_channels")

_OFF_TOPIC_PATTERNS = (
    re.compile(r"\btell me a joke\b", re.I),
    re.compile(r"\bjoke\b", re.I),
    re.compile(r"\bwhat(?:'s| is) the weather\b", re.I),
    re.compile(r"\bweather (?:in|for|today)\b", re.I),
    re.compile(r"\bwho won (?:the )?(?:super bowl|world series)\b", re.I),
)

_DURATION_PHRASE_RE = re.compile(
    r"\b(?:next|coming|following)\s+\d+\s*(?:day|week|month|year)s?\b|"
    r"\b\d+\s*(?:day|week|month|year)s?\s+(?:supply|budget|window)\b|"
    r"\bfor\s+(?:the\s+)?(?:rest|remainder)\s+of\s+the\s+year\b|"
    r"\bremaining\s+year\b",
    re.I,
)

_MEDICARE_SIGNAL_RE = re.compile(
    r"\b(?:mg|mcg|plan|tier|copay|coinsurance|medicare|formulary|deductible|"
    r"prescription|pharmacy|ytd|days[\s-]?supply)\b|[A-Za-z]\d{4}-\d{3}",
    re.I,
)

_OOS_TOPIC_RE = re.compile(
    r"\b(?:weather|joke|super bowl|enroll me|sign me up|medical advice)\b",
    re.I,
)

_OFF_TOPIC_REDIRECT = (
    "I can only help with Medicare prescription drug costs and plan lookups. "
    "Please ask about a specific drug, strength, and plan — for example, "
    "'metformin 500 mg on plan S5921-400'."
)


def _explanation_with_disclaimer(explanation: str) -> str:
    """Inline disclaimer for System early-return paths (off-topic, limit_reached)."""
    text = explanation.strip()
    disclaimer = settings.disclaimer_text
    if disclaimer and disclaimer not in text:
        return f"{text}\n\n{disclaimer}"
    return text


def _insulin_policy_explanation() -> str:
    return (
        "The $35 insulin rule is a ceiling, not a guaranteed price: a covered insulin "
        "product can cost less when its CMS plan cost-share is below the ceiling, and it "
        "can be $0 in catastrophic coverage. Name the specific insulin product and plan "
        "to get its CMS estimate; insulin does not use a deductible phase."
    )

# Stable key under which every estimate_drug_cost_all_channels call this turn is appended (as a
# list), so a second call in the same turn doesn't overwrite the first the way
# tool_artifacts["estimate_drug_cost_all_channels"] (last-call-wins) does. lookup_plan and
# normalize_drug stay single-shot and keep the plain dict-keyed behavior.
_MULTI_CHANNEL_CALLS_KEY = "estimate_drug_cost_all_channels__calls"


def _record_tool_artifact(
    tool_artifacts: dict[str, Any], name: str, artifact: dict[str, Any]
) -> None:
    tool_artifacts[name] = artifact
    if name == "estimate_drug_cost_all_channels":
        tool_artifacts.setdefault(_MULTI_CHANNEL_CALLS_KEY, []).append(artifact)


def _last_tool_call_key(arguments: dict[str, Any]) -> str:
    drug = str(arguments.get("drug_name") or "").strip().lower()
    if drug:
        return drug
    plan = str(arguments.get("plan_key") or "").strip().lower()
    if plan:
        return f"__plan_{plan}"
    return ""


def _record_last_tool_call(
    calls_by_drug: dict[str, dict[str, Any]], name: str, arguments: dict[str, Any]
) -> None:
    """Keeps one entry per drug so a multi-drug turn doesn't lose earlier drugs to
    last-call-wins, while a re-call for the same drug (e.g. a correction) still overwrites."""
    key = _last_tool_call_key(arguments) or f"__unkeyed_{len(calls_by_drug)}"
    calls_by_drug[key] = {"name": name, "arguments": arguments}


def _merge_last_tool_calls(
    prior: list[dict] | None, new_calls: list[dict]
) -> list[dict]:
    """Retain prior drugs when a follow-up turn re-estimates only a subset (e.g. one of two
    drugs in a basket). New calls for the same drug overwrite the prior entry."""
    merged: dict[str, dict[str, Any]] = {}
    for call in prior or []:
        args = call.get("arguments") or {}
        key = _last_tool_call_key(args) or f"__prior_{len(merged)}"
        merged[key] = call
    for call in new_calls:
        args = call.get("arguments") or {}
        key = _last_tool_call_key(args) or f"__new_{len(merged)}"
        merged[key] = call
    return list(merged.values())


async def _channel_estimates_for_guardrails(
    tool_artifacts: dict[str, dict[str, Any]],
    last_tool_calls: list[dict] | None,
) -> list[dict[str, Any]]:
    """Channel estimate dicts for guardrails without mutating the live artifact map."""
    estimates = channel_estimates_from_artifact(tool_artifacts)
    if estimates:
        return [est.model_dump() for est in estimates]
    preview: dict[str, dict[str, Any]] = dict(tool_artifacts)
    if _MULTI_CHANNEL_CALLS_KEY in tool_artifacts:
        preview[_MULTI_CHANNEL_CALLS_KEY] = list(tool_artifacts[_MULTI_CHANNEL_CALLS_KEY])
    channel = await ensure_channel_estimate(preview, last_tool_calls)
    if channel is None:
        return []
    return [channel.model_dump()]


async def ensure_channel_estimate(
    tool_artifacts: dict[str, dict[str, Any]],
    last_tool_calls: list[dict] | None,
) -> MultiChannelDrugCostEstimate | None:
    """Single source of truth for the UI: same multi-channel payload the agent estimated."""
    channel = channel_estimate_from_artifact(tool_artifacts)
    if channel is not None:
        return channel
    last_tool_call = last_tool_calls[-1] if last_tool_calls else None
    if not last_tool_call or last_tool_call.get("name") not in _ESTIMATE_TOOL_NAMES:
        return None
    args = last_tool_call.get("arguments") or {}
    plan_key = args.get("plan_key")
    drug_name = args.get("drug_name")
    if not plan_key or not drug_name:
        return None
    result = await estimate_drug_cost_all_channels(
        plan_key=str(plan_key),
        drug_name=str(drug_name),
        dosage=args.get("dosage"),
        days_supply=int(args.get("days_supply") or 30),
        ytd_oop_spend=float(args.get("ytd_oop_spend") or 0),
    )
    serialized = serialize_tool_result(result)
    if serialized.get("status") != "ok" or not serialized.get("data"):
        return None
    tool_artifacts["estimate_drug_cost_all_channels"] = serialized
    return channel_estimate_from_artifact(tool_artifacts)


def _parsed_plan_in_message(message: str) -> bool:
    return bool(re.search(r"\b[A-Za-z]\d{4}-\d{3}\b", message))


def _extract_explicit_ytd_from_message(message: str) -> float | None:
    """Deterministic YTD parse for any turn, not just insulin/mixed-basket messages.

    Reuses the same anchored regexes (ytd/year-to-date/spent keyword required) as the
    insulin path so a bare drug-less follow-up like "what if I've already spent $800
    on prescriptions this year?" still updates ytd_oop_spend instead of relying on the
    LLM to notice and re-call the tool.
    """
    from medicare_navigator.agent.insulin_requests import (
        _YTD_RE,
        _YTD_SUFFIX_RE,
        _parse_ytd_amount,
    )

    for pattern in (_YTD_SUFFIX_RE, _YTD_RE):
        match = pattern.search(message)
        if match:
            amount = _parse_ytd_amount(match.group(1))
            if amount is not None:
                return amount
    return None


def _is_pure_off_topic(message: str) -> bool:
    if _MEDICARE_SIGNAL_RE.search(message):
        return False
    return any(pattern.search(message) for pattern in _OFF_TOPIC_PATTERNS)


def _mixed_intent_note(message: str) -> str | None:
    if not _OOS_TOPIC_RE.search(message):
        return None
    if not _MEDICARE_SIGNAL_RE.search(message):
        return None
    return (
        "Mixed-intent message: refuse weather, jokes, enrollment, or other out-of-scope parts "
        "first in one brief sentence. Do not call estimate tools until each named drug has an "
        "explicit strength (dosage) and a plan_key is known."
    )


def _format_filters_context(filters: QuerySlots | None) -> str:
    if not filters:
        return ""
    parts = []
    if filters.drug:
        parts.append(f"drug={filters.drug}")
    if filters.dosage:
        parts.append(f"dosage={filters.dosage}")
    if filters.plan_id:
        parts.append(f"plan_id={filters.plan_id}")
    if filters.contract_year is not None:
        parts.append(f"contract_year={filters.contract_year}")
    if filters.days_supply is not None:
        parts.append(f"days_supply={filters.days_supply}")
    if filters.ytd_oop_spend is not None:
        parts.append(f"ytd_oop_spend={filters.ytd_oop_spend}")
    if not parts:
        return ""
    return "User pre-selected filters: " + ", ".join(parts)


def _format_history(
    chat_history: list[dict] | None,
    max_turns: int | None = None,
) -> str:
    if not chat_history:
        return ""
    if max_turns is None:
        max_turns = settings.max_chat_turns
    recent = chat_history[-(max_turns * 2) :]
    lines = ["Recent conversation:"]
    for entry in recent:
        role = entry.get("role", "user").capitalize()
        content = entry.get("content", "")
        if len(content) > 400:
            content = content[:400] + "..."
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _format_last_tool_calls(last_tool_calls: list[dict] | None) -> str:
    calls = [
        c
        for c in (last_tool_calls or [])
        if c.get("name") in ("estimate_drug_cost", "estimate_drug_cost_all_channels")
    ]
    if not calls:
        return ""
    if len(calls) == 1:
        name = calls[0].get("name")
        args = calls[0].get("arguments") or {}
        return (
            "Last cost estimate call: "
            f"{name}({json.dumps(args)}). If the user's new message states a fact that changes "
            "the cost inputs (e.g. deductible met/not met, different days supply, different "
            "pharmacy channel), you MUST re-call this tool with the same plan_key/drug_name/"
            "dosage/days_supply and an updated ytd_oop_spend (or other changed argument) — do "
            "not reuse the previous answer's dollar figures without a new tool call. When the "
            "user changes only YTD out-of-pocket spend, keep the same days_supply from this "
            "call unless they explicitly ask for a different supply length, and state which "
            "days_supply you used in your answer (e.g. 'for a 90-day supply')."
        )
    lines = ["Last cost estimate calls (multiple drugs from the prior turn — all of them, not just one):"]
    for call in calls:
        lines.append(f"- {call.get('name')}({json.dumps(call.get('arguments') or {})})")
    lines.append(
        "If the user's new message states a fact that changes the cost inputs (e.g. deductible "
        "met/not met, different days supply, different pharmacy channel), or asks a question "
        "about the prior calculation (e.g. days supply used, which channel), answer for EVERY "
        "drug listed above, not just one. Re-call the relevant tool(s) with the same plan_key/"
        "drug_name/dosage/days_supply and updated arguments where inputs changed — do not reuse "
        "previous dollar figures without a new tool call. When the user changes only YTD "
        "out-of-pocket spend, keep the same days_supply from the prior call unless they "
        "explicitly ask for a different supply length, and state which days_supply you used."
    )
    return "\n".join(lines)


def _format_date_context(date_context: "MediatorRewrite | None") -> str:
    """Sequential-orchestration guidance for compound date-range/duration asks the
    deterministic resolvers couldn't handle (e.g. a mixed basket over a custom window).
    The window's calendar end date is never computed here or by the model — only described
    in terms of the raw components the mediator extracted; see agent/mediator.py."""
    if date_context is None:
        return ""
    if date_context.duration_count is None and date_context.explicit_month is None:
        return ""
    if date_context.explicit_month is not None:
        parts = [f"month={date_context.explicit_month}", f"day={date_context.explicit_day}"]
        if date_context.explicit_year is not None:
            parts.append(f"year={date_context.explicit_year}")
        window_desc = "an explicit start date (" + ", ".join(parts) + ")"
    else:
        anchor = "today" if date_context.anchor_today else "an unspecified anchor"
        window_desc = f"{date_context.duration_count} {date_context.duration_unit} from {anchor}"
    return (
        f"The user's request spans a date/budget window: {window_desc}. If projecting cost "
        "across more than one fill in this window, call the estimate tool once per fill "
        "period, carrying ytd_oop_spend forward as the running total of the actual cost "
        "returned by each prior call in this window — never reuse a static ytd_oop_spend "
        "across calls, and never state a total that is not exactly the sum of what these "
        "tool calls returned. Do not compute the window's calendar end date yourself from "
        "these components; describe the period in terms of what was given."
    )


def _build_initial_messages(
    message: str,
    chat_history: list[dict] | None,
    filters: QuerySlots | None,
    last_tool_calls: list[dict] | None = None,
    date_context: "MediatorRewrite | None" = None,
) -> list[dict[str, Any]]:
    blocks = []
    history = _format_history(chat_history)
    if history:
        blocks.append(history)
    filter_ctx = _format_filters_context(filters)
    if filter_ctx:
        blocks.append(filter_ctx)
    last_call_ctx = _format_last_tool_calls(last_tool_calls)
    if last_call_ctx:
        blocks.append(last_call_ctx)
    date_ctx = _format_date_context(date_context)
    if date_ctx:
        blocks.append(date_ctx)
    mixed = _mixed_intent_note(message)
    if mixed:
        blocks.append(mixed)
    blocks.append(f"Current user message: {message}")
    return [{"role": "user", "content": "\n\n".join(blocks)}]


def _openai_tool_result_message(tool_call_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": tool_result_json(result),
    }


def _anthropic_tool_result_messages(
    tool_calls: list,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    content = []
    for call, result in zip(tool_calls, results):
        content.append(
            {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": tool_result_json(result),
            }
        )
    return {"role": "user", "content": content}


def _extract_response_fields(
    tool_artifacts: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None, DrugCostEstimate | None, dict[str, str]]:
    drug_name = None
    rxcui = None
    estimate = None
    data_as_of: dict[str, str] = {}

    for tool_name in (
        "estimate_drug_cost_all_channels",
        "estimate_drug_cost",
        "lookup_plan",
        "normalize_drug",
    ):
        result = tool_artifacts.get(tool_name)
        if result and result.get("as_of_date"):
            data_as_of[tool_name] = result["as_of_date"]

    for tool_name in ("estimate_drug_cost_all_channels", "estimate_drug_cost"):
        result = tool_artifacts.get(tool_name)
        if not result or not result.get("data"):
            continue
        data = result["data"]
        drug_name = data.get("drug_name")
        rxcui = data.get("rxcui")
        if result.get("status") in ("ok", "not_covered", "quantity_limit_blocked"):
            estimate = estimate_from_artifact(tool_artifacts)
            data_as_of["estimate"] = result.get("as_of_date", "")
        break

    return drug_name, rxcui, estimate, data_as_of


def _log_query(
    query_id: str,
    session_id: str | None,
    tools: list[str],
    statuses: dict[str, str],
    latency_ms: float,
) -> None:
    try:
        from medicare_navigator.storage.connection import DuckDBConnection

        db = DuckDBConnection()
        conn = db.connect()
        conn.execute(
            "INSERT INTO query_log VALUES (?, ?, ?, ?, ?, current_timestamp)",
            [
                query_id,
                session_id or "",
                json.dumps(tools),
                json.dumps(statuses),
                latency_ms,
            ],
        )
        conn.close()
    except Exception:
        pass


def _build_llm_usage(model_id: str, usage: TokenUsage) -> LlmUsage:
    spec = resolve_model(model_id)
    total = usage.total_tokens
    cost = estimate_cost_usd(spec, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
    return LlmUsage(
        model=spec.id,
        provider=spec.provider,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=total,
        cost_usd=cost,
    )


def _build_combined_usage(
    mediator_usage: LlmUsage | None, primary_usage: LlmUsage | None
) -> CombinedUsage | None:
    if mediator_usage is None and primary_usage is None:
        return None
    parts = [u for u in (mediator_usage, primary_usage) if u is not None]
    return CombinedUsage(
        input_tokens=sum(p.input_tokens for p in parts),
        output_tokens=sum(p.output_tokens for p in parts),
        total_tokens=sum(p.total_tokens for p in parts),
        cost_usd=round(sum(p.cost_usd for p in parts), 6),
    )


class Navigator:
    async def _run_deterministic_insulin_request(
        self,
        request,
        session,
        message: str,
        *,
        budget_start_date: "date | None" = None,
    ) -> QueryResponse:
        """Estimate every explicitly named insulin before prose generation.

        This prevents the model from dropping a product or converting a
        multi-product cap question into one pooled estimate.
        """
        from medicare_navigator.agent.insulin_requests import (
            format_insulin_estimate_sentence,
            insulin_policy_preamble,
        )

        tool_name = (
            "estimate_drug_cost"
            if request.pharmacy_channel
            else "estimate_drug_cost_all_channels"
        )
        tool_artifacts: dict[str, Any] = {}
        calls: list[dict[str, Any]] = []
        last_calls: list[dict[str, Any]] = []
        plan_keys = (
            request.plan_keys
            if len(request.plan_keys) >= 2
            else (request.plan_key,)
        )
        for plan_key in plan_keys:
            if not plan_key:
                continue
            for product in request.products:
                arguments = {
                    "plan_key": plan_key,
                    "drug_name": product,
                    "days_supply": request.days_supply,
                    "ytd_oop_spend": request.ytd_oop_spend,
                }
                if request.pharmacy_channel:
                    arguments["pharmacy_channel"] = request.pharmacy_channel
                # Only estimate_drug_cost_all_channels computes remaining_year_* fields
                # (agent/insulin_requests.py reads them only for INSULIN_INTENT_REMAINING_YEAR);
                # harmless to pass otherwise since the formatter simply won't use them.
                if budget_start_date is not None and tool_name == "estimate_drug_cost_all_channels":
                    arguments["budget_start_date"] = budget_start_date
                artifact = await call_tool(tool_name, arguments)
                serialized = (
                    serialize_tool_result(artifact)
                    if not isinstance(artifact, dict)
                    else artifact
                )
                calls.append(serialized)
                last_calls.append({"name": tool_name, "arguments": arguments})

        tool_artifacts[tool_name] = calls[-1]
        if tool_name == "estimate_drug_cost_all_channels":
            tool_artifacts[_MULTI_CHANNEL_CALLS_KEY] = calls

        explanations: list[str] = []
        policy_preamble = insulin_policy_preamble(request.intent)
        if policy_preamble:
            explanations.append(policy_preamble)

        call_index = 0
        for plan_key in plan_keys:
            if not plan_key:
                continue
            for product in request.products:
                artifact = calls[call_index]
                call_index += 1
                explanations.append(
                    format_insulin_estimate_sentence(
                        product=product,
                        plan_key=plan_key,
                        days_supply=request.days_supply,
                        artifact=artifact,
                        intent=request.intent,
                        pharmacy_channel=request.pharmacy_channel,
                    )
                )

        if len(request.products) > 1:
            explanations.append(
                "The insulin ceiling applies separately to each product; these products are "
                "not pooled into one $35 monthly total."
            )
        explanations.append(
            "These are CMS government reference estimates for the current quarter, not "
            "real-time pharmacy prices."
        )
        explanation = "\n\n".join(explanations)
        citations = build_citations_from_artifacts(tool_artifacts)
        channel_estimates = channel_estimates_from_artifact(tool_artifacts)
        explanation, citations, _ = apply_guardrails(
            explanation,
            tool_artifacts,
            citations,
            channel_estimates=channel_estimates,
        )
        if len(request.products) > 1 or len(plan_keys) > 1:
            # The generic channel repair logic is intentionally single-primary-call oriented.
            # Reapply our per-product rendering after validation so a later not_covered result
            # cannot erase an earlier covered product or the explicit pooled-cap correction.
            explanation = _explanation_with_disclaimer("\n\n".join(explanations))
        session_manager.set_last_tool_calls(session, last_calls)
        session_manager.append_turn(session, message, explanation)
        return QueryResponse(
            query_id=str(uuid.uuid4()),
            session_id=session["session_id"],
            status="ok",
            drug_name=request.products[0],
            estimate=estimate_from_artifact(tool_artifacts),
            channel_estimate=channel_estimates[0] if channel_estimates else None,
            channel_estimates=channel_estimates,
            explanation=explanation,
            citations=citations,
            disclaimer=settings.disclaimer_text,
            tools_invoked=[tool_name],
            tool_statuses={tool_name: calls[-1].get("status", "unknown")},
            response_source="System/Insulin",
        )

    async def _try_extraction_resolvers(
        self,
        message: str,
        *,
        log_message: str,
        filter_slots: QuerySlots | None,
        filter_plan_id: str | None,
        session: dict,
        query_id: str,
        start: float,
        date_context: "MediatorRewrite | None" = None,
    ) -> QueryResponse | None:
        """Deterministic slot-extraction resolvers (OOP, alternatives, insulin, mixed
        basket, dosage clarification) — as opposed to the raw-text safety gate in run()
        (off-topic/medical-advice/enrollment/invalid-input), which never sees mediator-
        rewritten text. Called against mediator-normalized text first when the mediator is
        enabled (see run()), then retried against the pre-mediator text if nothing matched.

        `message` is whichever text this pass is matching against; `log_message` is always
        the true pre-mediator text — chat_history must never show the user a paraphrase of
        their own message.
        """
        from medicare_navigator.agent.alternatives_questions import resolve_alternatives_question
        from medicare_navigator.agent.dosage_questions import (
            drugs_missing_dosage,
            resolve_dosage_question,
            should_clarify_dosage_before_estimate,
        )
        from medicare_navigator.agent.insulin_requests import (
            message_names_non_insulin_cost_drugs,
            resolve_insulin_request,
        )
        from medicare_navigator.agent.mixed_basket_requests import (
            build_batch_requests,
            build_mixed_basket_explanation,
            batch_result_to_artifact,
            resolve_mixed_basket_request,
        )
        from medicare_navigator.agent.oop_questions import resolve_oop_question
        from medicare_navigator.agent.tier_questions import resolve_tier_question
        from medicare_navigator.guardrails.citations import apply_guardrails, build_citations_from_artifacts
        from medicare_navigator.tools.batch_estimate import run_batch_estimates

        tier_result = await resolve_tier_question(
            message,
            filter_plan_id=filter_plan_id,
            filter_drug=filter_slots.drug if filter_slots else None,
        )
        if tier_result:
            explanation, tool_artifacts, tools_invoked = tier_result
            citations = build_citations_from_artifacts(tool_artifacts)
            explanation, citations, _ = apply_guardrails(
                explanation, tool_artifacts, citations
            )
            tool_statuses = {
                name: artifact.get("status", "unknown")
                for name, artifact in tool_artifacts.items()
            }
            latency = (time.perf_counter() - start) * 1000
            _log_query(query_id, session["session_id"], tools_invoked, tool_statuses, latency)
            session_manager.append_turn(session, log_message, explanation, query_id=query_id)
            return QueryResponse(
                query_id=query_id,
                session_id=session["session_id"],
                status="ok",
                explanation=explanation,
                citations=citations,
                disclaimer=settings.disclaimer_text,
                tools_invoked=tools_invoked,
                tool_statuses=tool_statuses,
                response_source="System/Tier",
            )

        oop_result = resolve_oop_question(message, filter_plan_id=filter_plan_id)
        if oop_result:
            explanation, tool_artifacts, tools_invoked = oop_result
            citations = build_citations_from_artifacts(tool_artifacts)
            explanation, citations, _ = apply_guardrails(
                explanation, tool_artifacts, citations
            )
            tool_statuses = {
                name: artifact.get("status", "unknown")
                for name, artifact in tool_artifacts.items()
            }
            latency = (time.perf_counter() - start) * 1000
            _log_query(query_id, session["session_id"], tools_invoked, tool_statuses, latency)
            session_manager.append_turn(session, log_message, explanation, query_id=query_id)
            return QueryResponse(
                query_id=query_id,
                session_id=session["session_id"],
                status="ok",
                explanation=explanation,
                citations=citations,
                disclaimer=settings.disclaimer_text,
                tools_invoked=tools_invoked,
                tool_statuses=tool_statuses,
                response_source="System/OOP",
            )

        alternatives_result = resolve_alternatives_question(message)
        if alternatives_result:
            explanation, tool_artifacts, tools_invoked = alternatives_result
            citations = build_citations_from_artifacts(tool_artifacts)
            explanation, citations, _ = apply_guardrails(
                explanation, tool_artifacts, citations
            )
            latency = (time.perf_counter() - start) * 1000
            _log_query(query_id, session["session_id"], tools_invoked, {}, latency)
            session_manager.append_turn(session, log_message, explanation, query_id=query_id)
            return QueryResponse(
                query_id=query_id,
                session_id=session["session_id"],
                status="ok",
                explanation=explanation,
                citations=citations,
                disclaimer=settings.disclaimer_text,
                tools_invoked=tools_invoked,
                tool_statuses={},
                response_source="System/Alternatives",
            )

        insulin_request = resolve_insulin_request(
            message,
            filter_plan_id=filter_plan_id,
            filter_days_supply=filter_slots.days_supply if filter_slots else None,
            filter_ytd_oop_spend=filter_slots.ytd_oop_spend if filter_slots else None,
        )
        if insulin_request and insulin_request.is_policy_question:
            explanation = _explanation_with_disclaimer(_insulin_policy_explanation())
            session_manager.append_turn(session, log_message, explanation, query_id=query_id)
            return QueryResponse(
                query_id=query_id,
                session_id=session["session_id"],
                status="ok",
                explanation=explanation,
                disclaimer=settings.disclaimer_text,
                response_source="System/InsulinPolicy",
            )

        # MixedBasketRequest has no duration/date-window field at all (see build_batch_requests
        # below — only a single days_supply). Today, silently ignoring "the next 3 months" and
        # returning a confidently-wrong single-fill total was the exact bug this design set out
        # to fix — never take this deterministic path when a date/duration modifier is present;
        # fall through to the agent loop instead (Phase 3b). The mediator (when enabled) is the
        # precise signal; _DURATION_PHRASE_RE is a cheap regex fallback so this guard still holds
        # with MEDIATOR_ENABLED=False (the default) rather than only degrading gracefully when
        # the mediator is on.
        has_unhandled_date_window = (
            date_context is not None
            and (date_context.duration_count is not None or date_context.explicit_month is not None)
        ) or bool(_DURATION_PHRASE_RE.search(message))
        mixed_request = (
            None
            if has_unhandled_date_window
            else await resolve_mixed_basket_request(
                message,
                filter_plan_id=filter_plan_id,
                filter_days_supply=filter_slots.days_supply if filter_slots else None,
                filter_ytd_oop_spend=filter_slots.ytd_oop_spend if filter_slots else None,
                filter_drug=filter_slots.drug if filter_slots else None,
                filter_dosage=filter_slots.dosage if filter_slots else None,
            )
        )
        if mixed_request:
            batch_results = await run_batch_estimates(build_batch_requests(mixed_request))
            built_explanation = build_mixed_basket_explanation(mixed_request, batch_results)
            explanation = built_explanation
            tool_name = "estimate_drug_cost_all_channels"
            artifacts = [batch_result_to_artifact(r) for r in batch_results]
            tool_artifacts: dict[str, Any] = {tool_name: artifacts[-1]}
            tool_artifacts[_MULTI_CHANNEL_CALLS_KEY] = artifacts
            last_calls = [
                {
                    "name": tool_name,
                    "arguments": {
                        "plan_key": mixed_request.plan_key,
                        "drug_name": item.drug_name,
                        "dosage": item.dosage,
                        "days_supply": mixed_request.days_supply,
                        "ytd_oop_spend": mixed_request.ytd_oop_spend,
                    },
                }
                for item in mixed_request.items
            ]
            citations = build_citations_from_artifacts(tool_artifacts)
            channel_estimates = channel_estimates_from_artifact(tool_artifacts)
            explanation, citations, _ = apply_guardrails(
                explanation,
                tool_artifacts,
                citations,
                channel_estimates=channel_estimates,
            )
            if len(mixed_request.items) > 1:
                explanation = _explanation_with_disclaimer(built_explanation)
            session_manager.set_last_tool_calls(session, last_calls)
            session_manager.append_turn(session, log_message, explanation, query_id=query_id)
            latency = (time.perf_counter() - start) * 1000
            _log_query(
                query_id,
                session["session_id"],
                [tool_name],
                {tool_name: artifacts[-1].get("status", "unknown")},
                latency,
            )
            return QueryResponse(
                query_id=query_id,
                session_id=session["session_id"],
                status="ok",
                drug_name=mixed_request.items[0].drug_name,
                estimate=estimate_from_artifact(tool_artifacts),
                channel_estimate=channel_estimates[0] if channel_estimates else None,
                channel_estimates=channel_estimates,
                explanation=explanation,
                citations=citations,
                disclaimer=settings.disclaimer_text,
                tools_invoked=[tool_name],
                tool_statuses={tool_name: artifacts[-1].get("status", "unknown")},
                response_source="System/MixedBasket",
            )

        if insulin_request and insulin_request.products and (
            insulin_request.plan_key or len(insulin_request.plan_keys) >= 2
        ) and not message_names_non_insulin_cost_drugs(message):
            from medicare_navigator.agent.datetime_context import resolve_explicit_start_date

            budget_start_date = None
            if date_context is not None:
                budget_start_date = resolve_explicit_start_date(
                    date_context.explicit_month,
                    date_context.explicit_day,
                    date_context.explicit_year,
                )
            response = await self._run_deterministic_insulin_request(
                insulin_request,
                session,
                log_message,
                budget_start_date=budget_start_date,
            )
            response.query_id = query_id
            return response

        # When a plan is already known, defer single-drug strength gaps to the estimate
        # tool so suppressed-plan and insulin data-gap hard-stops run before needs_dosage.
        # Multi-drug asks still clarify here so the LLM does not guess strengths.
        plan_known = _parsed_plan_in_message(message) or bool(filter_plan_id)
        dosage_result = None
        if should_clarify_dosage_before_estimate(
            message,
            filter_drug=filter_slots.drug if filter_slots else None,
            filter_dosage=filter_slots.dosage if filter_slots else None,
            plan_known=plan_known,
        ):
            dosage_result = await resolve_dosage_question(
                message,
                filter_drug=filter_slots.drug if filter_slots else None,
                filter_dosage=filter_slots.dosage if filter_slots else None,
            )
        if dosage_result:
            explanation, tool_artifacts, tools_invoked = dosage_result
            explanation = _explanation_with_disclaimer(explanation)
            missing_drugs = drugs_missing_dosage(
                message,
                filter_drug=filter_slots.drug if filter_slots else None,
                filter_dosage=filter_slots.dosage if filter_slots else None,
            )
            if len(missing_drugs) == 1:
                # Narrow, purpose-built clarification state: only what a bare-strength
                # follow-up reply ("500mg") needs to be spliced back onto next turn.
                session_manager.set_pending_clarification(
                    session, {"type": "dosage", "drugs": missing_drugs}
                )
            latency = (time.perf_counter() - start) * 1000
            _log_query(query_id, session["session_id"], tools_invoked, {}, latency)
            session_manager.append_turn(session, log_message, explanation, query_id=query_id)
            return QueryResponse(
                query_id=query_id,
                session_id=session["session_id"],
                status="needs_clarification",
                explanation=explanation,
                disclaimer=settings.disclaimer_text,
                tools_invoked=tools_invoked,
                tool_statuses={},
                response_source="System/Dosage",
            )

        return None

    async def run(
        self,
        message: str,
        filter_slots: QuerySlots | None = None,
        session_id: str | None = None,
        llm_model: str | None = None,
        timezone: str | None = None,
    ) -> QueryResponse:
        start = time.perf_counter()
        query_id = str(uuid.uuid4())
        model_id = llm_model or default_llm_model()
        session = session_manager.get_or_create(session_id)
        chat_history = session.get("chat_history", [])

        if not session_manager.can_continue(session):
            explanation = _explanation_with_disclaimer(
                "This session has reached the maximum number of follow-up turns. "
                "Please start a new session."
            )
            return QueryResponse(
                query_id=query_id,
                session_id=session["session_id"],
                status="limit_reached",
                explanation=explanation,
                disclaimer=settings.disclaimer_text,
                response_source="System",
            )

        session_manager.increment_turn(session)
        last_tool_calls = session.get("last_tool_calls") or []

        if _is_pure_off_topic(message):
            explanation = _explanation_with_disclaimer(_OFF_TOPIC_REDIRECT)
            session_manager.append_turn(session, message, explanation)
            return QueryResponse(
                query_id=query_id,
                session_id=session["session_id"],
                status="ok",
                explanation=explanation,
                disclaimer=settings.disclaimer_text,
                response_source="System",
            )

        from medicare_navigator.agent.request_context import set_request_timezone
        from medicare_navigator.agent.enrollment_questions import resolve_enrollment_question
        from medicare_navigator.agent.invalid_input_questions import resolve_invalid_input_question
        from medicare_navigator.agent.mediator import rewrite_and_extract
        from medicare_navigator.agent.medical_advice_questions import resolve_medical_advice_question

        set_request_timezone(timezone)

        # Deterministic YTD carryover: a follow-up stating an explicit YTD dollar amount
        # (e.g. "I've already spent $800 on prescriptions this year") must update the
        # recalculation even when the message names no drug and so never reaches the
        # insulin/mixed-basket resolvers below — those are the only other callers of
        # _extract_ytd_oop_spend. Only overrides when the message actually states an
        # amount; otherwise filter_slots is left untouched.
        explicit_ytd = _extract_explicit_ytd_from_message(message)
        if explicit_ytd is not None:
            base = filter_slots.model_dump() if filter_slots else {}
            base["ytd_oop_spend"] = explicit_ytd
            base["raw_message"] = message
            filter_slots = QuerySlots(**base)

        filter_plan_id = filter_slots.plan_id if filter_slots else None

        # --- Safety gate: raw message only, never the mediator's rewrite below. A refusal
        # decision must never depend on how an LLM chose to rephrase the input. ---
        medical_advice_result = resolve_medical_advice_question(message)
        if medical_advice_result:
            explanation, tool_artifacts, tools_invoked = medical_advice_result
            explanation = _explanation_with_disclaimer(explanation)
            latency = (time.perf_counter() - start) * 1000
            _log_query(query_id, session["session_id"], tools_invoked, {}, latency)
            session_manager.append_turn(session, message, explanation, query_id=query_id)
            return QueryResponse(
                query_id=query_id,
                session_id=session["session_id"],
                status="ok",
                explanation=explanation,
                disclaimer=settings.disclaimer_text,
                tools_invoked=tools_invoked,
                tool_statuses={},
                response_source="System/MedicalAdvice",
            )

        enrollment_result = resolve_enrollment_question(message)
        if enrollment_result:
            explanation, tool_artifacts, tools_invoked = enrollment_result
            explanation = _explanation_with_disclaimer(explanation)
            latency = (time.perf_counter() - start) * 1000
            _log_query(query_id, session["session_id"], tools_invoked, {}, latency)
            session_manager.append_turn(session, message, explanation, query_id=query_id)
            return QueryResponse(
                query_id=query_id,
                session_id=session["session_id"],
                status="ok",
                explanation=explanation,
                disclaimer=settings.disclaimer_text,
                tools_invoked=tools_invoked,
                tool_statuses={},
                response_source="System/Enrollment",
            )

        invalid_input_result = resolve_invalid_input_question(message)
        if invalid_input_result:
            explanation, tool_artifacts, tools_invoked = invalid_input_result
            explanation = _explanation_with_disclaimer(explanation)
            latency = (time.perf_counter() - start) * 1000
            _log_query(query_id, session["session_id"], tools_invoked, {}, latency)
            session_manager.append_turn(session, message, explanation, query_id=query_id)
            return QueryResponse(
                query_id=query_id,
                session_id=session["session_id"],
                status="needs_clarification",
                explanation=explanation,
                disclaimer=settings.disclaimer_text,
                tools_invoked=tools_invoked,
                tool_statuses={},
                response_source="System/InvalidInput",
            )

        from medicare_navigator.agent.conversation_recall_questions import (
            resolve_conversation_recall_question,
        )

        recall_result = resolve_conversation_recall_question(message, chat_history)
        if recall_result:
            explanation, tool_artifacts, tools_invoked = recall_result
            explanation = _explanation_with_disclaimer(explanation)
            latency = (time.perf_counter() - start) * 1000
            _log_query(query_id, session["session_id"], tools_invoked, {}, latency)
            session_manager.append_turn(session, message, explanation, query_id=query_id)
            return QueryResponse(
                query_id=query_id,
                session_id=session["session_id"],
                status="ok",
                explanation=explanation,
                disclaimer=settings.disclaimer_text,
                tools_invoked=tools_invoked,
                tool_statuses={},
                response_source="System/ConversationRecall",
            )

        # --- Pending-clarification splice: deterministic, zero-cost, unconditional. Runs
        # even with the mediator disabled — a bare strength reply ("500mg") gets the pending
        # drug spliced back on before anything else sees it. ---
        pending = session.get("pending_clarification")
        effective_message = message
        if (
            pending
            and len(pending.get("drugs") or []) == 1
            and re.match(r"^\s*\d+(\.\d+)?\s*(mg|mcg|ml|units?)?\.?\s*$", message, re.I)
        ):
            effective_message = f"{pending['drugs'][0]} {message.strip()}"
        session_manager.set_pending_clarification(session, None)

        # --- Mediator: unconditional on every message that reaches here, when enabled.
        # Its output feeds every downstream stage below; it never runs before this point,
        # so it can never affect the safety-gate decisions above. ---
        resolver_input = effective_message
        mediated = None
        mediator_llm_usage: LlmUsage | None = None
        if settings.mediator_enabled:
            last_tool_call = last_tool_calls[-1] if last_tool_calls else None
            try:
                mediated, mediator_usage, mediator_model = await rewrite_and_extract(
                    effective_message,
                    last_tool_call=last_tool_call,
                    pending_clarification=pending,
                    timezone=timezone,
                )
            except Exception:
                # rewrite_and_extract is documented never to raise — this is defense in
                # depth, not the primary safety mechanism, so a bug there still degrades to
                # today's raw-text behavior for this turn rather than a 500.
                mediated = None
            if mediated is not None:
                resolver_input = mediated.normalized_message
                mediator_llm_usage = _build_llm_usage(mediator_model, mediator_usage)

        # --- Extraction resolvers: mediator-normalized text first, then a free local retry
        # against the pre-mediator text if nothing matched — insurance against the mediator
        # corrupting an already-parseable message, at the cost of a regex pass, not an LLM
        # call. ---
        response = await self._try_extraction_resolvers(
            resolver_input,
            log_message=effective_message,
            filter_slots=filter_slots,
            filter_plan_id=filter_plan_id,
            session=session,
            query_id=query_id,
            start=start,
            date_context=mediated,
        )
        resolver_match_source = "normalized" if response is not None else None
        if response is None and resolver_input != effective_message:
            response = await self._try_extraction_resolvers(
                effective_message,
                log_message=effective_message,
                filter_slots=filter_slots,
                filter_plan_id=filter_plan_id,
                session=session,
                query_id=query_id,
                start=start,
                date_context=mediated,
            )
            if response is not None:
                resolver_match_source = "raw_fallback"

        if settings.mediator_enabled:
            if mediated is None:
                outcome = "mediator_error"
            elif resolver_match_source == "normalized":
                outcome = "resolver_matched"
            elif resolver_match_source == "raw_fallback":
                outcome = "fell_back_to_raw"
            else:
                outcome = "fell_through_to_agent_loop"
            logger.info(
                "mediator_outcome query_id=%s outcome=%s rewrote=%s",
                query_id,
                outcome,
                mediated is not None and resolver_input != effective_message,
            )

        if response is not None:
            if mediator_llm_usage is not None:
                response.mediator_llm_usage = mediator_llm_usage
                response.total_llm_usage = _build_combined_usage(
                    mediator_llm_usage, response.llm_usage
                )
            return response

        explanation, tool_artifacts, tools_invoked, response_source, token_usage, new_last_tool_calls = (
            await self._run_agent_loop(
                resolver_input, filter_slots, chat_history, model_id, last_tool_calls, timezone,
                date_context=mediated,
            )
        )

        guardrail_channel_estimates = await _channel_estimates_for_guardrails(
            tool_artifacts, new_last_tool_calls
        )

        citations = build_citations_from_artifacts(tool_artifacts)
        explanation, citations, guard_errors = apply_guardrails(
            explanation,
            tool_artifacts,
            citations,
            channel_estimates=guardrail_channel_estimates,
            user_message=effective_message,
        )
        if guard_errors:
            (
                retry_explanation,
                retry_citations,
                retry_usage,
                _,
                retry_artifacts,
                retry_tools_invoked,
                retry_last_tool_calls,
            ) = await self._retry_after_guardrail(
                resolver_input,
                filter_slots,
                chat_history,
                tool_artifacts,
                guard_errors,
                model_id,
                timezone,
                last_tool_calls,
                mediated,
                guardrail_user_message=effective_message,
            )
            token_usage = token_usage + retry_usage
            tool_artifacts.update(retry_artifacts)
            for name in retry_tools_invoked:
                if name not in tools_invoked:
                    tools_invoked.append(name)
            if retry_last_tool_calls:
                new_last_tool_calls = retry_last_tool_calls
            if retry_explanation:
                explanation = retry_explanation
                citations = retry_citations

        if new_last_tool_calls:
            prior_last_tool_calls = session.get("last_tool_calls") or []
            merged_last_tool_calls = _merge_last_tool_calls(
                prior_last_tool_calls, new_last_tool_calls
            )
            session_manager.set_last_tool_calls(session, merged_last_tool_calls)

        drug_name, rxcui, estimate, data_as_of = _extract_response_fields(tool_artifacts)
        channel_estimate = await ensure_channel_estimate(tool_artifacts, new_last_tool_calls)
        channel_estimates = channel_estimates_from_artifact(tool_artifacts)
        if channel_estimate is not None:
            channel_based = estimate_from_artifact(
                {"estimate_drug_cost_all_channels": {
                    "status": "ok",
                    "data": channel_estimate.model_dump(),
                }}
            )
            if channel_based and (estimate is None or estimate.cost_low is None):
                estimate = channel_based
                if channel_estimate.drug_name:
                    drug_name = channel_estimate.drug_name
                if channel_estimate.rxcui:
                    rxcui = channel_estimate.rxcui
        tool_statuses = {
            name: artifact.get("status", "unknown")
            for name, artifact in tool_artifacts.items()
            if name in tools_invoked
        }

        status = "ok"
        lower_explanation = explanation.lower()
        if any(
            artifact.get("status") == "needs_dosage"
            for artifact in tool_artifacts.values()
            if isinstance(artifact, dict)
        ):
            status = "needs_clarification"
        elif "strength" in lower_explanation and (
            "specify" in lower_explanation or "required" in lower_explanation
        ):
            status = "needs_clarification"
        elif "which drug" in lower_explanation:
            status = "needs_clarification"
        elif "which medicare plan" in lower_explanation or (
            "which plan" in lower_explanation and "plan" in effective_message.lower()
        ):
            status = "needs_clarification"
        else:
            result = tool_artifacts.get("estimate_drug_cost_all_channels") or tool_artifacts.get(
                "estimate_drug_cost"
            )
            if result and result.get("status") in ("not_found", "no_match"):
                status = "not_found"
            else:
                lookup = tool_artifacts.get("lookup_plan")
                if (
                    lookup
                    and lookup.get("status") == "not_found"
                    and _parsed_plan_in_message(effective_message)
                ):
                    status = "not_found"

        latency = (time.perf_counter() - start) * 1000
        _log_query(query_id, session["session_id"], tools_invoked, tool_statuses, latency)
        session_manager.append_turn(session, effective_message, explanation, query_id=query_id)

        primary_llm_usage = _build_llm_usage(model_id, token_usage)
        return QueryResponse(
            query_id=query_id,
            session_id=session["session_id"],
            status=status,
            drug_name=drug_name,
            rxcui=rxcui,
            estimate=estimate,
            channel_estimate=channel_estimate,
            channel_estimates=channel_estimates,
            explanation=explanation,
            citations=citations,
            disclaimer=settings.disclaimer_text,
            data_as_of=data_as_of,
            tools_invoked=tools_invoked,
            tool_statuses=tool_statuses,
            response_source=response_source,
            llm_usage=primary_llm_usage,
            mediator_llm_usage=mediator_llm_usage,
            total_llm_usage=_build_combined_usage(mediator_llm_usage, primary_llm_usage),
        )

    async def _run_agent_loop(
        self,
        message: str,
        filter_slots: QuerySlots | None,
        chat_history: list[dict] | None,
        model_id: str,
        last_tool_calls: list[dict] | None = None,
        timezone: str | None = None,
        date_context: "MediatorRewrite | None" = None,
    ) -> tuple[str, dict[str, dict[str, Any]], list[str], str, TokenUsage, list[dict]]:
        messages = _build_initial_messages(
            message, chat_history, filter_slots, last_tool_calls, date_context
        )
        tool_artifacts: dict[str, dict[str, Any]] = {}
        tools_invoked: list[str] = []
        spec = resolve_model(model_id)
        tools = openai_tools() if spec.provider == "openai" else anthropic_tools()
        is_openai = spec.provider == "openai"
        token_usage = TokenUsage()
        new_last_tool_calls_by_drug: dict[str, dict[str, Any]] = {}
        system_prompt = build_navigator_system_prompt(timezone)

        explanation = ""
        for _ in range(settings.max_tool_rounds):
            result = await llm_client.chat_with_tools(
                system_prompt, messages, tools, model=model_id
            )
            token_usage = token_usage + result.usage

            if result.tool_calls:
                if is_openai:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": result.content,
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.name,
                                        "arguments": json.dumps(tc.arguments),
                                    },
                                }
                                for tc in result.tool_calls
                            ],
                        }
                    )
                else:
                    content_blocks: list[dict[str, Any]] = []
                    if result.content:
                        content_blocks.append({"type": "text", "text": result.content})
                    for tc in result.tool_calls:
                        content_blocks.append(
                            {
                                "type": "tool_use",
                                "id": tc.id,
                                "name": tc.name,
                                "input": tc.arguments,
                            }
                        )
                    messages.append({"role": "assistant", "content": content_blocks})

                batch_results: list[dict[str, Any]] = []
                for tc in result.tool_calls:
                    artifact = await call_tool(tc.name, tc.arguments)
                    _record_tool_artifact(tool_artifacts, tc.name, artifact)
                    if tc.name not in tools_invoked:
                        tools_invoked.append(tc.name)
                    if tc.name in ("estimate_drug_cost", "estimate_drug_cost_all_channels"):
                        _record_last_tool_call(new_last_tool_calls_by_drug, tc.name, tc.arguments)
                    batch_results.append(artifact)

                if is_openai:
                    for tc, artifact in zip(result.tool_calls, batch_results):
                        messages.append(_openai_tool_result_message(tc.id, artifact))
                else:
                    messages.append(
                        _anthropic_tool_result_messages(result.tool_calls, batch_results)
                    )
                continue

            if result.content:
                explanation = result.content
                break

        if not explanation:
            raise LLMRequestError(
                "Navigator agent did not produce a response within the maximum tool rounds."
            )

        return (
            explanation,
            tool_artifacts,
            tools_invoked,
            llm_client.model_label(model_id),
            token_usage,
            list(new_last_tool_calls_by_drug.values()),
        )

    async def _retry_after_guardrail(
        self,
        message: str,
        filter_slots: QuerySlots | None,
        chat_history: list[dict] | None,
        tool_artifacts: dict[str, dict[str, Any]],
        errors: list[str],
        model_id: str,
        timezone: str | None = None,
        last_tool_calls: list[dict] | None = None,
        date_context: "MediatorRewrite | None" = None,
        guardrail_user_message: str | None = None,
    ) -> tuple[str | None, list, TokenUsage, list[str], dict[str, dict[str, Any]], list[str], list[dict]]:
        retry_messages = _build_initial_messages(
            message, chat_history, filter_slots, last_tool_calls, date_context
        )
        retry_messages.append(
            {
                "role": "user",
                "content": (
                    "Your prior answer failed validation:\n"
                    + "\n".join(f"- {e}" for e in errors)
                    + "\nIf a benefit phase or cost input changed (e.g. deductible met/not met), "
                    "re-call the estimate tool with updated arguments before answering — do not "
                    "guess. Otherwise rewrite using ONLY dollar amounts and phase language "
                    "supported by tool results (channels.*.cost_low/cost_high when the tool "
                    "returned a channels object, or top-level cost_low/cost_high, plus "
                    "benefit_phase/effective_phase). $0.00 is a valid estimate — do not claim "
                    "no dollar figure exists when any channel has a numeric cost."
                ),
            }
        )
        spec = resolve_model(model_id)
        tools = openai_tools() if spec.provider == "openai" else anthropic_tools()
        is_openai = spec.provider == "openai"
        merged_artifacts = dict(tool_artifacts)
        if _MULTI_CHANNEL_CALLS_KEY in merged_artifacts:
            # dict(...) is shallow — copy the list so retry appends don't mutate the caller's.
            merged_artifacts[_MULTI_CHANNEL_CALLS_KEY] = list(
                merged_artifacts[_MULTI_CHANNEL_CALLS_KEY]
            )
        retry_tools_invoked: list[str] = []
        new_last_tool_calls_by_drug: dict[str, dict[str, Any]] = {}
        token_usage = TokenUsage()
        system_prompt = build_navigator_system_prompt(timezone)

        for _ in range(settings.max_tool_rounds):
            try:
                result = await llm_client.chat_with_tools(
                    system_prompt, retry_messages, tools, model=model_id
                )
            except Exception:
                return (
                    None,
                    [],
                    token_usage,
                    errors,
                    merged_artifacts,
                    retry_tools_invoked,
                    list(new_last_tool_calls_by_drug.values()),
                )
            token_usage = token_usage + result.usage

            if result.tool_calls:
                if is_openai:
                    retry_messages.append(
                        {
                            "role": "assistant",
                            "content": result.content,
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.name,
                                        "arguments": json.dumps(tc.arguments),
                                    },
                                }
                                for tc in result.tool_calls
                            ],
                        }
                    )
                else:
                    content_blocks: list[dict[str, Any]] = []
                    if result.content:
                        content_blocks.append({"type": "text", "text": result.content})
                    for tc in result.tool_calls:
                        content_blocks.append(
                            {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                        )
                    retry_messages.append({"role": "assistant", "content": content_blocks})

                batch_results: list[dict[str, Any]] = []
                for tc in result.tool_calls:
                    artifact = await call_tool(tc.name, tc.arguments)
                    _record_tool_artifact(merged_artifacts, tc.name, artifact)
                    if tc.name not in retry_tools_invoked:
                        retry_tools_invoked.append(tc.name)
                    if tc.name in ("estimate_drug_cost", "estimate_drug_cost_all_channels"):
                        _record_last_tool_call(new_last_tool_calls_by_drug, tc.name, tc.arguments)
                    batch_results.append(artifact)

                if is_openai:
                    for tc, artifact in zip(result.tool_calls, batch_results):
                        retry_messages.append(_openai_tool_result_message(tc.id, artifact))
                else:
                    retry_messages.append(
                        _anthropic_tool_result_messages(result.tool_calls, batch_results)
                    )
                continue

            new_last_tool_calls = list(new_last_tool_calls_by_drug.values())
            if not result.content:
                return None, [], token_usage, errors, merged_artifacts, retry_tools_invoked, new_last_tool_calls

            guardrail_channel_estimates = await _channel_estimates_for_guardrails(
                merged_artifacts, new_last_tool_calls
            )
            citations = build_citations_from_artifacts(merged_artifacts)
            explanation, citations, retry_errors = apply_guardrails(
                result.content,
                merged_artifacts,
                citations,
                channel_estimates=guardrail_channel_estimates,
                user_message=guardrail_user_message,
            )
            if retry_errors:
                return None, [], token_usage, errors, merged_artifacts, retry_tools_invoked, new_last_tool_calls
            return explanation, citations, token_usage, [], merged_artifacts, retry_tools_invoked, new_last_tool_calls

        return (
            None,
            [],
            token_usage,
            errors,
            merged_artifacts,
            retry_tools_invoked,
            list(new_last_tool_calls_by_drug.values()),
        )


navigator = Navigator()
