from __future__ import annotations

import json
import time
import uuid
from typing import Any

from medicare_navigator.agent.prompts import NAVIGATOR_SYSTEM_PROMPT
from medicare_navigator.config import settings
from medicare_navigator.guardrails.citations import (
    apply_guardrails,
    build_citations_from_artifacts,
    channel_estimate_from_artifact,
    estimate_from_artifact,
)
from medicare_navigator.llm.client import llm_client
from medicare_navigator.llm.errors import LLMRequestError
from medicare_navigator.llm.models import DEFAULT_LLM_MODEL, estimate_cost_usd, resolve_model
from medicare_navigator.llm.types import TokenUsage
from medicare_navigator.mcp.registry import call_tool, serialize_tool_result, tool_result_json
from medicare_navigator.mcp.schemas import anthropic_tools, openai_tools
from medicare_navigator.models.query import QuerySlots
from medicare_navigator.models.response import DrugCostEstimate, LlmUsage, MultiChannelDrugCostEstimate, QueryResponse
from medicare_navigator.session.manager import session_manager
from medicare_navigator.tools.estimate_drug_cost import estimate_drug_cost_all_channels


_ESTIMATE_TOOL_NAMES = ("estimate_drug_cost", "estimate_drug_cost_all_channels")


async def ensure_channel_estimate(
    tool_artifacts: dict[str, dict[str, Any]],
    last_tool_call: dict | None,
) -> MultiChannelDrugCostEstimate | None:
    """Single source of truth for the UI: same multi-channel payload the agent estimated."""
    channel = channel_estimate_from_artifact(tool_artifacts)
    if channel is not None:
        return channel
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
    tool_artifacts["estimate_drug_cost_all_channels"] = serialize_tool_result(result)
    return channel_estimate_from_artifact(tool_artifacts)


def _parsed_plan_in_message(message: str) -> bool:
    import re

    return bool(re.search(r"\b[A-Za-z]\d{4}-\d{3}\b", message))


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
    if filters.days_supply is not None:
        parts.append(f"days_supply={filters.days_supply}")
    if filters.ytd_oop_spend is not None:
        parts.append(f"ytd_oop_spend={filters.ytd_oop_spend}")
    if not parts:
        return ""
    return "User pre-selected filters: " + ", ".join(parts)


def _format_history(chat_history: list[dict] | None, max_turns: int = 3) -> str:
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


def _format_last_tool_call(last_tool_call: dict | None) -> str:
    if not last_tool_call:
        return ""
    name = last_tool_call.get("name")
    if name not in ("estimate_drug_cost", "estimate_drug_cost_all_channels"):
        return ""
    args = last_tool_call.get("arguments") or {}
    return (
        "Last cost estimate call: "
        f"{name}({json.dumps(args)}). If the user's new message states a fact that changes "
        "the cost inputs (e.g. deductible met/not met, different days supply, different "
        "pharmacy channel), you MUST re-call this tool with the same plan_key/drug_name/"
        "dosage/days_supply and an updated ytd_oop_spend (or other changed argument) — do "
        "not reuse the previous answer's dollar figures without a new tool call."
    )


def _build_initial_messages(
    message: str,
    chat_history: list[dict] | None,
    filters: QuerySlots | None,
    last_tool_call: dict | None = None,
) -> list[dict[str, Any]]:
    blocks = []
    history = _format_history(chat_history)
    if history:
        blocks.append(history)
    filter_ctx = _format_filters_context(filters)
    if filter_ctx:
        blocks.append(filter_ctx)
    last_call_ctx = _format_last_tool_call(last_tool_call)
    if last_call_ctx:
        blocks.append(last_call_ctx)
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


class Navigator:
    async def run(
        self,
        message: str,
        filter_slots: QuerySlots | None = None,
        session_id: str | None = None,
        llm_model: str | None = None,
    ) -> QueryResponse:
        start = time.perf_counter()
        query_id = str(uuid.uuid4())
        model_id = llm_model or DEFAULT_LLM_MODEL
        session = session_manager.get_or_create(session_id)
        chat_history = session.get("chat_history", [])

        if not session_manager.can_continue(session):
            explanation = (
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
        last_tool_call = session.get("last_tool_call")

        explanation, tool_artifacts, tools_invoked, response_source, token_usage, new_last_tool_call = (
            await self._run_agent_loop(
                message, filter_slots, chat_history, model_id, last_tool_call
            )
        )

        citations = build_citations_from_artifacts(tool_artifacts)
        explanation, citations, guard_errors = apply_guardrails(
            explanation, tool_artifacts, citations
        )
        if guard_errors:
            (
                retry_explanation,
                retry_citations,
                retry_usage,
                _,
                retry_artifacts,
                retry_tools_invoked,
                retry_last_tool_call,
            ) = await self._retry_after_guardrail(
                message,
                filter_slots,
                chat_history,
                tool_artifacts,
                guard_errors,
                model_id,
            )
            token_usage = token_usage + retry_usage
            tool_artifacts.update(retry_artifacts)
            for name in retry_tools_invoked:
                if name not in tools_invoked:
                    tools_invoked.append(name)
            if retry_last_tool_call:
                new_last_tool_call = retry_last_tool_call
            if retry_explanation:
                explanation = retry_explanation
                citations = retry_citations

        if new_last_tool_call:
            session_manager.set_last_tool_call(
                session, new_last_tool_call["name"], new_last_tool_call["arguments"]
            )

        drug_name, rxcui, estimate, data_as_of = _extract_response_fields(tool_artifacts)
        channel_estimate = await ensure_channel_estimate(tool_artifacts, new_last_tool_call)
        tool_statuses = {
            name: artifact.get("status", "unknown")
            for name, artifact in tool_artifacts.items()
            if name in tools_invoked
        }

        status = "ok"
        lower_explanation = explanation.lower()
        if "which drug" in lower_explanation:
            status = "needs_clarification"
        elif "which medicare plan" in lower_explanation or (
            "which plan" in lower_explanation and "plan" in message.lower()
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
                    and _parsed_plan_in_message(message)
                ):
                    status = "not_found"

        latency = (time.perf_counter() - start) * 1000
        _log_query(query_id, session["session_id"], tools_invoked, tool_statuses, latency)
        session_manager.append_turn(session, message, explanation, query_id=query_id)

        return QueryResponse(
            query_id=query_id,
            session_id=session["session_id"],
            status=status,
            drug_name=drug_name,
            rxcui=rxcui,
            estimate=estimate,
            channel_estimate=channel_estimate,
            explanation=explanation,
            citations=citations,
            disclaimer=settings.disclaimer_text,
            data_as_of=data_as_of,
            tools_invoked=tools_invoked,
            tool_statuses=tool_statuses,
            response_source=response_source,
            llm_usage=_build_llm_usage(model_id, token_usage),
        )

    async def _run_agent_loop(
        self,
        message: str,
        filter_slots: QuerySlots | None,
        chat_history: list[dict] | None,
        model_id: str,
        last_tool_call: dict | None = None,
    ) -> tuple[str, dict[str, dict[str, Any]], list[str], str, TokenUsage, dict | None]:
        messages = _build_initial_messages(message, chat_history, filter_slots, last_tool_call)
        tool_artifacts: dict[str, dict[str, Any]] = {}
        tools_invoked: list[str] = []
        spec = resolve_model(model_id)
        tools = openai_tools() if spec.provider == "openai" else anthropic_tools()
        is_openai = spec.provider == "openai"
        token_usage = TokenUsage()
        new_last_tool_call: dict | None = None

        explanation = ""
        for _ in range(settings.max_tool_rounds):
            result = await llm_client.chat_with_tools(
                NAVIGATOR_SYSTEM_PROMPT, messages, tools, model=model_id
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
                    tool_artifacts[tc.name] = artifact
                    if tc.name not in tools_invoked:
                        tools_invoked.append(tc.name)
                    if tc.name in ("estimate_drug_cost", "estimate_drug_cost_all_channels"):
                        new_last_tool_call = {"name": tc.name, "arguments": tc.arguments}
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
            new_last_tool_call,
        )

    async def _retry_after_guardrail(
        self,
        message: str,
        filter_slots: QuerySlots | None,
        chat_history: list[dict] | None,
        tool_artifacts: dict[str, dict[str, Any]],
        errors: list[str],
        model_id: str,
    ) -> tuple[str | None, list, TokenUsage, list[str], dict[str, dict[str, Any]], list[str], dict | None]:
        retry_messages = _build_initial_messages(message, chat_history, filter_slots)
        retry_messages.append(
            {
                "role": "user",
                "content": (
                    "Your prior answer failed validation:\n"
                    + "\n".join(f"- {e}" for e in errors)
                    + "\nIf a benefit phase or cost input changed (e.g. deductible met/not met), "
                    "re-call the estimate tool with updated arguments before answering — do not "
                    "guess. Otherwise rewrite using ONLY dollar amounts and phase language "
                    "supported by tool results (cost_low/cost_high, benefit_phase/"
                    "effective_phase)."
                ),
            }
        )
        spec = resolve_model(model_id)
        tools = openai_tools() if spec.provider == "openai" else anthropic_tools()
        is_openai = spec.provider == "openai"
        merged_artifacts = dict(tool_artifacts)
        retry_tools_invoked: list[str] = []
        new_last_tool_call: dict | None = None
        token_usage = TokenUsage()

        for _ in range(settings.max_tool_rounds):
            try:
                result = await llm_client.chat_with_tools(
                    NAVIGATOR_SYSTEM_PROMPT, retry_messages, tools, model=model_id
                )
            except Exception:
                return None, [], token_usage, errors, merged_artifacts, retry_tools_invoked, new_last_tool_call
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
                    merged_artifacts[tc.name] = artifact
                    if tc.name not in retry_tools_invoked:
                        retry_tools_invoked.append(tc.name)
                    if tc.name in ("estimate_drug_cost", "estimate_drug_cost_all_channels"):
                        new_last_tool_call = {"name": tc.name, "arguments": tc.arguments}
                    batch_results.append(artifact)

                if is_openai:
                    for tc, artifact in zip(result.tool_calls, batch_results):
                        retry_messages.append(_openai_tool_result_message(tc.id, artifact))
                else:
                    retry_messages.append(
                        _anthropic_tool_result_messages(result.tool_calls, batch_results)
                    )
                continue

            if not result.content:
                return None, [], token_usage, errors, merged_artifacts, retry_tools_invoked, new_last_tool_call

            citations = build_citations_from_artifacts(merged_artifacts)
            explanation, citations, retry_errors = apply_guardrails(
                result.content, merged_artifacts, citations
            )
            if retry_errors:
                return None, [], token_usage, errors, merged_artifacts, retry_tools_invoked, new_last_tool_call
            return explanation, citations, token_usage, [], merged_artifacts, retry_tools_invoked, new_last_tool_call

        return None, [], token_usage, errors, merged_artifacts, retry_tools_invoked, new_last_tool_call


navigator = Navigator()
