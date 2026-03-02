from __future__ import annotations

import asyncio
import json
import time
import uuid

from medicare_navigator.agents.clarification import run_clarification_agent
from medicare_navigator.agents.policy import run_policy_agent
from medicare_navigator.agents.synthesis import run_synthesis_agent
from medicare_navigator.config import settings
from medicare_navigator.intake.agent import run_intake
from medicare_navigator.models.query import IntakeResult, QuerySlots
from medicare_navigator.models.response import QueryResponse
from medicare_navigator.models.tool_result import ToolResult
from medicare_navigator.session.manager import session_manager
from medicare_navigator.storage.connection import DuckDBConnection
from medicare_navigator.tools.alternatives import alternatives_finder
from medicare_navigator.tools.cost_trend import cost_trend_lookup
from medicare_navigator.tools.formulary_benefit import formulary_benefit_lookup


async def _retry_async(fn, max_retries: int = 2):
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                await asyncio.sleep(0.5 * (2**attempt))
    raise last_exc


def _log_query(
    query_id: str,
    session_id: str | None,
    tools: list[str],
    agents: list[str],
    statuses: dict[str, str],
    latency_ms: float,
) -> None:
    try:
        db = DuckDBConnection()
        conn = db.connect()
        conn.execute(
            "INSERT INTO query_log VALUES (?, ?, ?, ?, ?, ?, current_timestamp)",
            [
                query_id,
                session_id or "",
                json.dumps(tools),
                json.dumps(agents),
                json.dumps(statuses),
                latency_ms,
            ],
        )
        conn.close()
    except Exception:
        pass


def _artifact_key(name: str, params: dict) -> str:
    return f"{name}:{json.dumps(params, sort_keys=True)}"


def _build_response(
    query_id: str,
    session: dict,
    status: str,
    explanation: str,
    *,
    clarification_message: str | None = None,
    drug_name: str | None = None,
    rxcui: str | None = None,
    formulary_data=None,
    trend_data=None,
    alt_data=None,
    citations=None,
    data_as_of=None,
    tools_invoked=None,
    agents_invoked=None,
    tool_statuses=None,
    response_source: str = "System",
) -> QueryResponse:
    return QueryResponse(
        query_id=query_id,
        session_id=session["session_id"],
        status=status,
        drug_name=drug_name,
        rxcui=rxcui,
        formulary=formulary_data,
        cost_trend=trend_data or [],
        alternatives=alt_data or [],
        explanation=explanation,
        citations=citations or [],
        clarification_message=clarification_message,
        disclaimer=settings.disclaimer_text,
        data_as_of=data_as_of or {},
        tools_invoked=tools_invoked or [],
        agents_invoked=agents_invoked or [],
        tool_statuses=tool_statuses or {},
        response_source=response_source,
    )


class Orchestrator:
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
            return _build_response(
                query_id,
                session,
                "limit_reached",
                explanation,
                response_source="System",
            )

        session_manager.increment_turn(session)
        tools_invoked: list[str] = []
        agents_invoked: list[str] = ["intake"]
        tool_artifacts: dict[str, ToolResult] = {}
        tool_statuses: dict[str, str] = {}
        data_as_of: dict[str, str] = {}

        intake: IntakeResult = await run_intake(
            message,
            filter_slots=filter_slots,
            session_slots=session["slots"],
            chat_history=chat_history,
        )
        session["slots"] = intake.slots

        if intake.status != "complete" or not intake.parsed_query:
            explanation, clarification_source = await run_clarification_agent(
                message, intake, chat_history=chat_history
            )
            agents_invoked.append("clarification")
            latency = (time.perf_counter() - start) * 1000
            _log_query(query_id, session["session_id"], tools_invoked, agents_invoked, tool_statuses, latency)
            session_manager.append_turn(session, message, explanation, query_id=query_id)
            return _build_response(
                query_id,
                session,
                intake.status,
                explanation,
                clarification_message=explanation,
                tools_invoked=tools_invoked,
                agents_invoked=agents_invoked,
                response_source=clarification_source,
            )

        parsed = intake.parsed_query
        session["parsed_query"] = parsed

        reuse_artifacts = (
            intake.follow_up_type == "clarify_count"
            and intake.slots_unchanged
            and session.get("last_tool_artifacts")
        )

        if reuse_artifacts:
            tool_artifacts = dict(session["last_tool_artifacts"])
            for name, result in tool_artifacts.items():
                tool_statuses[name] = result.status.value
                if name == "formulary_benefit_lookup":
                    data_as_of["formulary"] = result.as_of_date
                elif name == "cost_trend_lookup":
                    data_as_of["spending"] = result.as_of_date
                elif name == "alternatives_finder":
                    data_as_of["alternatives"] = result.as_of_date
        else:
            if parsed.plan_key and parsed.ndc:
                key = _artifact_key(
                    "formulary",
                    {"plan": parsed.plan_key, "ndc": parsed.ndc, "ytd": parsed.ytd_oop_spend},
                )
                if key in session["tool_artifacts"]:
                    form_result = session["tool_artifacts"][key]
                else:
                    tools_invoked.append("formulary_benefit_lookup")
                    form_result = formulary_benefit_lookup(
                        parsed.plan_key,
                        parsed.ndc,
                        parsed.ytd_oop_spend,
                        parsed.contract_year,
                        ytd_oop_spend_provided=parsed.ytd_oop_spend_provided,
                    )
                    session["tool_artifacts"][key] = form_result
                tool_artifacts["formulary_benefit_lookup"] = form_result
                tool_statuses["formulary_benefit_lookup"] = form_result.status.value
                data_as_of["formulary"] = form_result.as_of_date

            if parsed.include_cost_trend and parsed.rxcui:
                tools_invoked.append("cost_trend_lookup")
                trend_result = cost_trend_lookup(parsed.rxcui)
                tool_artifacts["cost_trend_lookup"] = trend_result
                tool_statuses["cost_trend_lookup"] = trend_result.status.value
                data_as_of["spending"] = trend_result.as_of_date

            if parsed.include_alternatives and parsed.rxcui:
                tools_invoked.append("alternatives_finder")
                alt_result = alternatives_finder(parsed.rxcui)
                tool_artifacts["alternatives_finder"] = alt_result
                tool_statuses["alternatives_finder"] = alt_result.status.value
                data_as_of["alternatives"] = alt_result.as_of_date

        policy_claims = None
        needs_policy = (
            "explain_cost_change" not in parsed.intents and "explain" in message.lower()
        )
        if needs_policy and not reuse_artifacts:
            agents_invoked.append("policy")
            policy_out, retrieval = await run_policy_agent(parsed, tool_artifacts)
            tool_artifacts["policy_retrieval"] = retrieval
            tools_invoked.append("policy_retrieval")
            tool_statuses["policy_retrieval"] = retrieval.status.value
            policy_claims = [c.model_dump() for c in policy_out.claims]

        agents_invoked.append("synthesis")
        explanation, citations, response_source = await run_synthesis_agent(
            parsed,
            tool_artifacts,
            policy_claims,
            chat_history=chat_history,
            follow_up_type=intake.follow_up_type,
        )

        if tool_artifacts:
            session["last_tool_artifacts"] = dict(tool_artifacts)

        formulary_data = None
        form_art = tool_artifacts.get("formulary_benefit_lookup")
        if form_art and form_art.status.value in ("ok", "not_covered") and form_art.data:
            formulary_data = form_art.data

        trend_data = []
        trend_art = tool_artifacts.get("cost_trend_lookup")
        if trend_art and trend_art.status.value == "ok" and trend_art.data:
            trend_data = trend_art.data

        alt_data = []
        alt_art = tool_artifacts.get("alternatives_finder")
        if alt_art and alt_art.status.value == "ok" and alt_art.data:
            alt_data = alt_art.data

        latency = (time.perf_counter() - start) * 1000
        _log_query(query_id, session["session_id"], tools_invoked, agents_invoked, tool_statuses, latency)
        session_manager.append_turn(session, message, explanation, query_id=query_id)

        return _build_response(
            query_id,
            session,
            "ok",
            explanation,
            drug_name=parsed.drug_name,
            rxcui=parsed.rxcui,
            formulary_data=formulary_data,
            trend_data=trend_data,
            alt_data=alt_data,
            citations=citations,
            data_as_of=data_as_of,
            tools_invoked=tools_invoked,
            agents_invoked=agents_invoked,
            tool_statuses=tool_statuses,
            response_source=response_source,
        )


orchestrator = Orchestrator()
