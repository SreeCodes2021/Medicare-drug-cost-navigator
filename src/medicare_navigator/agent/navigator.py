from __future__ import annotations

import json
import time
import uuid
from typing import Any

from medicare_navigator.agent.fallback import run_fallback_navigator
from medicare_navigator.agent.prompts import NAVIGATOR_SYSTEM_PROMPT
from medicare_navigator.config import settings
from medicare_navigator.guardrails.citations import apply_guardrails, build_citations_from_artifacts
from medicare_navigator.llm.client import llm_client
from medicare_navigator.mcp.registry import call_tool, tool_result_json
from medicare_navigator.mcp.schemas import anthropic_tools, openai_tools
from medicare_navigator.models.query import QuerySlots
from medicare_navigator.models.response import (
    AlternativesResult,
    CostTrendPoint,
    FormularyResult,
    QueryResponse,
)
from medicare_navigator.session.manager import session_manager


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


def _build_initial_messages(
    message: str,
    chat_history: list[dict] | None,
    filters: QuerySlots | None,
) -> list[dict[str, Any]]:
    blocks = []
    history = _format_history(chat_history)
    if history:
        blocks.append(history)
    filter_ctx = _format_filters_context(filters)
    if filter_ctx:
        blocks.append(filter_ctx)
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
) -> tuple[str | None, str | None, FormularyResult | None, list[CostTrendPoint], list[AlternativesResult], dict[str, str]]:
    drug_name = None
    rxcui = None
    formulary = None
    trends: list[CostTrendPoint] = []
    alts: list[AlternativesResult] = []
    data_as_of: dict[str, str] = {}

    norm = tool_artifacts.get("normalize_drug")
    if norm and norm.get("data"):
        selected = norm["data"].get("selected") or (norm["data"].get("candidates") or [{}])[0]
        drug_name = selected.get("drug_name")
        rxcui = selected.get("rxcui")

    form = tool_artifacts.get("formulary_benefit_lookup")
    if form and form.get("data") and form.get("status") in ("ok", "not_covered"):
        formulary = FormularyResult.model_validate(form["data"])
        data_as_of["formulary"] = form.get("as_of_date", "")

    trend = tool_artifacts.get("cost_trend_lookup")
    if trend and trend.get("status") == "ok" and trend.get("data"):
        trends = [CostTrendPoint.model_validate(p) for p in trend["data"]]
        data_as_of["spending"] = trend.get("as_of_date", "")

    alt = tool_artifacts.get("alternatives_finder")
    if alt and alt.get("status") == "ok" and alt.get("data"):
        alts = [AlternativesResult.model_validate(a) for a in alt["data"]]
        data_as_of["alternatives"] = alt.get("as_of_date", "")

    return drug_name, rxcui, formulary, trends, alts, data_as_of


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
            "INSERT INTO query_log VALUES (?, ?, ?, ?, ?, ?, current_timestamp)",
            [
                query_id,
                session_id or "",
                json.dumps(tools),
                json.dumps(["navigator"]),
                json.dumps(statuses),
                latency_ms,
            ],
        )
        conn.close()
    except Exception:
        pass


class Navigator:
    async def run(
        self,
        message: str,
        filter_slots: QuerySlots | None = None,
        session_id: str | None = None,
    ) -> QueryResponse:
        start = time.perf_counter()
        query_id = str(uuid.uuid4())
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

        if not llm_client._has_credentials():
            explanation, tool_artifacts, tools_invoked = await run_fallback_navigator(
                message, filter_slots, chat_history
            )
            response_source = llm_client.fallback_label("navigator")
        else:
            explanation, tool_artifacts, tools_invoked, response_source = (
                await self._run_agent_loop(message, filter_slots, chat_history)
            )

        citations = build_citations_from_artifacts(tool_artifacts)
        explanation, citations, guard_errors = apply_guardrails(
            explanation, tool_artifacts, citations
        )
        if guard_errors and llm_client._has_credentials():
            retry_explanation, retry_citations, _ = await self._retry_after_guardrail(
                message,
                filter_slots,
                chat_history,
                tool_artifacts,
                guard_errors,
            )
            if retry_explanation:
                explanation = retry_explanation
                citations = retry_citations

        drug_name, rxcui, formulary, trends, alts, data_as_of = _extract_response_fields(
            tool_artifacts
        )
        tool_statuses = {
            name: artifact.get("status", "unknown")
            for name, artifact in tool_artifacts.items()
            if name in tools_invoked or name.endswith("_lookup") or name.endswith("_finder")
        }
        for name in tools_invoked:
            if name in tool_artifacts:
                tool_statuses[name] = tool_artifacts[name].get("status", "unknown")

        status = "ok"
        lower_explanation = explanation.lower()
        if "which drug" in lower_explanation:
            status = "needs_clarification"
        elif "which medicare plan" in lower_explanation or (
            "which plan" in lower_explanation and "plan" in message.lower()
        ):
            status = "needs_clarification"
        else:
            norm = tool_artifacts.get("normalize_drug")
            if norm and norm.get("status") in ("not_found", "no_match"):
                status = "not_found"
            else:
                form = tool_artifacts.get("formulary_benefit_lookup")
                if form and form.get("status") == "not_found":
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
            formulary=formulary,
            cost_trend=trends,
            alternatives=alts,
            explanation=explanation,
            citations=citations,
            disclaimer=settings.disclaimer_text,
            data_as_of=data_as_of,
            tools_invoked=tools_invoked,
            agents_invoked=["navigator"],
            tool_statuses=tool_statuses,
            response_source=response_source,
        )

    async def _run_agent_loop(
        self,
        message: str,
        filter_slots: QuerySlots | None,
        chat_history: list[dict] | None,
    ) -> tuple[str, dict[str, dict[str, Any]], list[str], str]:
        messages = _build_initial_messages(message, chat_history, filter_slots)
        tool_artifacts: dict[str, dict[str, Any]] = {}
        tools_invoked: list[str] = []
        tools = openai_tools() if llm_client.provider == "openai" else anthropic_tools()
        is_openai = llm_client.provider == "openai"

        explanation = ""
        for _ in range(settings.max_tool_rounds):
            result = await llm_client.chat_with_tools(
                NAVIGATOR_SYSTEM_PROMPT, messages, tools
            )

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
            explanation, tool_artifacts, tools_invoked = await run_fallback_navigator(
                message, filter_slots, chat_history
            )
            return explanation, tool_artifacts, tools_invoked, llm_client.fallback_label(
                "navigator"
            )

        return explanation, tool_artifacts, tools_invoked, llm_client.model_label()

    async def _retry_after_guardrail(
        self,
        message: str,
        filter_slots: QuerySlots | None,
        chat_history: list[dict] | None,
        tool_artifacts: dict[str, dict[str, Any]],
        errors: list[str],
    ) -> tuple[str | None, list, list[str]]:
        retry_messages = _build_initial_messages(message, chat_history, filter_slots)
        retry_messages.append(
            {
                "role": "user",
                "content": (
                    "Your prior answer failed validation:\n"
                    + "\n".join(f"- {e}" for e in errors)
                    + "\nRewrite using ONLY dollar amounts from tool results. "
                    "Include supply_estimate walkthrough if present."
                ),
            }
        )
        tools = openai_tools() if llm_client.provider == "openai" else anthropic_tools()
        try:
            result = await llm_client.chat_with_tools(
                NAVIGATOR_SYSTEM_PROMPT, retry_messages, tools
            )
        except Exception:
            return None, [], errors
        if not result.content or result.tool_calls:
            return None, [], errors
        citations = build_citations_from_artifacts(tool_artifacts)
        explanation, citations, _ = apply_guardrails(
            result.content, tool_artifacts, citations
        )
        return explanation, citations, []


navigator = Navigator()
