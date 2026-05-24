from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from medicare_navigator.llm.types import ChatWithToolsResult, ToolCallSpec
from medicare_navigator.storage.repository import DrugRepository

T = TypeVar("T", bound=BaseModel)

_FOLLOW_UP_COUNT_RE = re.compile(
    r"only one|how many alternative|did you find|just one|any other alternative|is that all",
    re.I,
)

_QUESTION_WORDS = frozenset(
    {
        "plan",
        "plans",
        "spent",
        "spend",
        "show",
        "what",
        "which",
        "tier",
        "copay",
        "alternatives",
        "alternative",
        "cost",
        "costs",
        "the",
        "for",
        "and",
        "only",
        "find",
        "many",
        "that",
        "have",
        "you",
        "did",
        "want",
        "help",
        "with",
        "buy",
        "pieces",
        "year",
        "already",
        "budgeting",
        "eligible",
        "eligibility",
        "filling",
        "cover",
        "covers",
        "covered",
        "live",
        "state",
        "medicare",
        "drug",
        "name",
        "check",
        "look",
        "how",
        "many",
        "find",
        "did",
    }
)


@dataclass
class ParsedMessage:
    drug: str | None = None
    dosage: str | None = None
    plan_key: str | None = None
    ytd_oop_spend: float | None = None
    ytd_provided: bool = False
    quantity: int | None = None
    wants_trend: bool = False
    wants_alternatives: bool = False
    wants_policy: bool = False


def _current_message(user_prompt: str) -> str:
    text = user_prompt.lower()
    if "current message:" in text:
        return user_prompt.split("Current message:", 1)[-1].split("\n", 1)[0]
    return user_prompt


def _drug_token_from_message(message: str) -> str | None:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", message)
    repo = DrugRepository()
    best: str | None = None
    best_len = 0
    for token in tokens:
        lower = token.lower()
        if lower in _QUESTION_WORDS:
            continue
        try:
            matches = repo.lookup_by_name(token)
        except Exception:
            matches = []
        if matches and len(token) > best_len:
            best = token
            best_len = len(token)
    if best:
        return best.lower()

    for_match = re.search(r"\bfor\s+([a-zA-Z][a-zA-Z0-9-]+)", message, re.I)
    if for_match:
        candidate = for_match.group(1)
        if candidate.lower() not in _QUESTION_WORDS:
            return candidate.lower()

    tokens = sorted(re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", message), key=len, reverse=True)
    for token in tokens:
        if token.lower() not in _QUESTION_WORDS:
            return token.lower()
    return None


def parse_message(message: str) -> ParsedMessage:
    text = message.lower()
    parsed = ParsedMessage()
    parsed.drug = _drug_token_from_message(message)

    dose_match = re.search(r"(\d+)\s*mg", text)
    if dose_match:
        parsed.dosage = f"{dose_match.group(1)}mg"

    plan_match = re.search(r"plan\s+([A-Za-z0-9]+-\d{3})", message, re.I)
    if not plan_match:
        plan_match = re.search(r"\b([A-Za-z]\d{4}-\d{3})\b", message, re.I)
    if plan_match:
        parsed.plan_key = plan_match.group(1).upper()

    for pattern in [
        r"spent\s+\$?\s*(\d+(?:\.\d+)?)",
        r"\$(\d+(?:\.\d+)?)\s+ytd",
        r"spent\s+(\d+(?:\.\d+)?)",
        r"spend\s+\$?\s*(\d+(?:\.\d+)?)",
    ]:
        spend_match = re.search(pattern, text)
        if spend_match:
            parsed.ytd_oop_spend = float(spend_match.group(1))
            parsed.ytd_provided = True
            break

    qty_match = re.search(r"(\d+)\s*(?:pieces|tablets|pills|units)", text)
    if qty_match:
        parsed.quantity = int(qty_match.group(1))

    parsed.wants_trend = any(
        k in text for k in ("trend", "went up", "go up", "increase", "change", "why")
    )
    parsed.wants_alternatives = "alternative" in text
    parsed.wants_policy = parsed.wants_trend or any(
        k in text
        for k in (
            "explain",
            "phase",
            "deductible",
            "coverage gap",
            "catastrophic",
        )
    )
    return parsed


def _intake_output(user_prompt: str, model: type[T]) -> T:
    current_message = _current_message(user_prompt)
    text = current_message.lower()

    is_follow_up = "recent conversation:" in user_prompt.lower()
    follow_up_type = None
    intents = ["tier_lookup"]
    drug = None
    dosage = None
    plan_id = None
    ytd = None

    if is_follow_up and _FOLLOW_UP_COUNT_RE.search(current_message):
        follow_up_type = "clarify_count"
        intents = ["alternatives"]
    else:
        parsed = parse_message(current_message)
        drug = parsed.drug
        dosage = parsed.dosage
        plan_id = parsed.plan_key
        ytd = parsed.ytd_oop_spend
        if "alternative" in text:
            intents = ["alternatives"]
        elif "why" in text or "change" in text or "went up" in text:
            intents = ["explain_cost_change"]

    return model.model_validate(
        {
            "drug": drug,
            "dosage": dosage,
            "plan_id": plan_id,
            "ytd_oop_spend": ytd,
            "intents": intents,
            "confidence": 0.8 if drug else 0.2,
            "is_follow_up": is_follow_up,
            "follow_up_type": follow_up_type,
        }
    )


def _clarification_output(user_prompt: str, model: type[T]) -> T:
    if "Context:" in user_prompt:
        json_part = user_prompt.split("Context:", 1)[1].strip()
        try:
            ctx = json.loads(json_part)
            missing = ctx.get("missing_slots") or []
            resolved = ctx.get("resolved_drug") or {}
            if "plan_id" in missing and resolved:
                name = resolved.get("drug_name", "the medication")
                dosage = resolved.get("dosage")
                label = f"{name} {dosage}".strip() if dosage else name
                return model.model_validate(
                    {
                        "message": (
                            f"I found {label}. To check eligibility, which plan should I look up? "
                            "You can provide a plan ID like H1234-045 or a plan name."
                        )
                    }
                )
            candidates = ctx.get("drug_candidates") or []
            if candidates and ctx.get("status") == "not_found":
                names = ", ".join(
                    c.get("drug_name", "") for c in candidates[:3] if c.get("drug_name")
                )
                if names:
                    return model.model_validate(
                        {"message": f"Did you mean {names}? Please confirm the drug name."}
                    )
        except json.JSONDecodeError:
            pass
    return model.model_validate({"message": "Which drug would you like help with?"})


def _policy_output(model: type[T]) -> T:
    return model.model_validate(
        {
            "claims": [
                {
                    "claim": "Part D benefit phases and tier placement affect what beneficiaries pay.",
                    "source_id": "cms_part_d_redesign_2026",
                }
            ]
        }
    )


def _synthesis_output(model: type[T]) -> T:
    return model.model_validate(
        {
            "explanation": (
                "Based on the retrieved government data for your query, the drug's formulary tier "
                "and cost-sharing apply under your plan's benefit phase. See the structured results "
                "and citations for specific figures."
            ),
            "citations": [],
        }
    )


def mock_structured_completion(
    user_prompt: str,
    response_model: type[T],
    agent_name: str = "agent",
) -> T:
    name = response_model.__name__
    if name == "IntakeLLMOutput":
        return _intake_output(user_prompt, response_model)
    if name == "PolicyLLMOutput":
        return _policy_output(response_model)
    if name == "SynthesisLLMOutput":
        return _synthesis_output(response_model)
    if name == "ClarificationLLMOutput":
        return _clarification_output(user_prompt, response_model)
    return response_model.model_validate({})


def _extract_user_message(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            text = content.strip()
            if "Current user message:" in text:
                return text.split("Current user message:", 1)[1].strip().split("\n\n")[0]
            return text
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = (block.get("text") or "").strip()
                    if text:
                        if "Current user message:" in text:
                            return text.split("Current user message:", 1)[1].strip().split("\n\n")[0]
                        return text
    return ""


def _tools_done(messages: list[dict[str, Any]]) -> set[str]:
    done: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    done.add(block.get("name", ""))
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            done.add(fn.get("name", ""))
    return {name for name in done if name}


def _tool_use_ids(messages: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        mapping[block["id"]] = block.get("name", "")
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                mapping[tc["id"]] = fn.get("name", "")
    return mapping


def _tool_result(messages: list[dict[str, Any]], tool_name: str) -> dict[str, Any] | None:
    id_to_name = _tool_use_ids(messages)
    for msg in reversed(messages):
        if msg.get("role") == "tool":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id", "")
            if id_to_name.get(tool_use_id) != tool_name:
                continue
            payload = block.get("content")
            if isinstance(payload, str):
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    return None
    return None


def _tool_call(name: str, arguments: dict[str, Any]) -> ToolCallSpec:
    return ToolCallSpec(id=f"mock_{uuid.uuid4().hex[:12]}", name=name, arguments=arguments)


def _build_final_explanation(
    message: str,
    parsed: ParsedMessage,
    norm: dict[str, Any],
    form: dict[str, Any] | None,
    trend: dict[str, Any] | None,
    alts: dict[str, Any] | None,
    policy: dict[str, Any] | None,
) -> str:
    selected = (norm.get("data") or {}).get("selected") or (norm.get("data") or {}).get(
        "candidates", [{}]
    )[0]
    drug_name = selected.get("drug_name", parsed.drug or "the drug")
    parts: list[str] = []

    if form and form.get("status") == "not_covered":
        parts.append(
            f"{drug_name} does not appear on the formulary for plan {parsed.plan_key}. "
            "Benefit phase and cost-sharing do not apply because the plan does not cover this drug."
        )
    elif form and form.get("status") == "ok" and form.get("data"):
        form_data = form["data"]
        cs = form_data.get("cost_share") or {}
        copay = cs.get("copay")
        tier = form_data.get("tier")
        phase = (form_data.get("benefit_phase") or "").replace("_", " ")
        if parsed.ytd_provided and parsed.ytd_oop_spend is not None:
            parts.append(
                f"With ${parsed.ytd_oop_spend:.2f} in year-to-date out-of-pocket spending, "
                f"you are in the {phase} phase on plan {parsed.plan_key}."
            )
        if tier is not None and copay is not None:
            parts.append(
                f"{drug_name.capitalize()} is tier {tier} with a ${copay:.2f} copay per fill "
                f"(as of {form.get('as_of_date', 'the latest available date')})."
            )

    if trend and trend.get("status") == "ok" and trend.get("data"):
        points = trend["data"]
        if len(points) >= 2:
            first, last = points[0], points[-1]
            if last.get("avg_unit_cost") and first.get("avg_unit_cost"):
                pct = (
                    (last["avg_unit_cost"] - first["avg_unit_cost"])
                    / first["avg_unit_cost"]
                    * 100
                )
                direction = "rose" if pct > 0 else "fell"
                parts.append(
                    f"Program average unit cost for {drug_name} {direction} about "
                    f"{abs(pct):.0f}% from {first['year']} to {last['year']}."
                )

    if alts and alts.get("status") == "ok" and alts.get("data"):
        names = ", ".join(a["drug_name"] for a in alts["data"][:3])
        parts.append(f"Therapeutically equivalent alternatives include: {names}.")

    if policy and policy.get("status") == "ok" and policy.get("data"):
        for passage in policy["data"][:2]:
            text = (passage.get("text") or "").strip()
            if text:
                parts.append(text)

    if not parts:
        parts.append("I retrieved data for your query but could not build a supported summary.")
    return " ".join(parts)


async def mock_chat_with_tools(
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> ChatWithToolsResult:
    message = _extract_user_message(messages)
    parsed = parse_message(message)
    done = _tools_done(messages)

    if "normalize_drug" not in done:
        if not parsed.drug:
            return ChatWithToolsResult(
                content=(
                    "Which drug would you like help with? I can look up formulary tier, "
                    "cost-sharing, and spending trends once you name the medication."
                )
            )
        args: dict[str, Any] = {"drug_name": parsed.drug}
        if parsed.dosage:
            args["dosage"] = parsed.dosage
        return ChatWithToolsResult(content=None, tool_calls=[_tool_call("normalize_drug", args)])

    norm = _tool_result(messages, "normalize_drug")
    if not norm or norm.get("status") != "ok" or not norm.get("data"):
        drug = parsed.drug or "that drug"
        return ChatWithToolsResult(
            content=(
                f"I could not find a match for '{drug}'. "
                "Please check the spelling or try a different drug name."
            )
        )

    selected = norm["data"].get("selected") or norm["data"]["candidates"][0]
    rxcui = selected.get("rxcui")
    ndc = selected.get("ndc")
    drug_name = selected.get("drug_name", parsed.drug)

    if parsed.plan_key and "lookup_plan" not in done:
        return ChatWithToolsResult(
            content=None,
            tool_calls=[_tool_call("lookup_plan", {"plan_key": parsed.plan_key})],
        )

    if not parsed.plan_key and not parsed.wants_alternatives:
        return ChatWithToolsResult(
            content=(
                f"I found {drug_name}. Which Medicare plan should I check "
                "(for example, plan S5678-012)?"
            )
        )

    if parsed.wants_alternatives and rxcui and not parsed.plan_key:
        if "alternatives_finder" not in done:
            return ChatWithToolsResult(
                content=None,
                tool_calls=[_tool_call("alternatives_finder", {"rxcui": rxcui})],
            )
        alts = _tool_result(messages, "alternatives_finder")
        if alts and alts.get("status") == "ok" and alts.get("data"):
            names = ", ".join(a["drug_name"] for a in alts["data"][:5])
            return ChatWithToolsResult(
                content=(
                    f"Therapeutically equivalent alternatives to {drug_name} include: {names}."
                )
            )

    if not parsed.plan_key:
        return ChatWithToolsResult(
            content=(
                f"I found {drug_name}. Which Medicare plan should I check "
                "(for example, plan S5678-012)?"
            )
        )

    if "formulary_benefit_lookup" not in done:
        form_args: dict[str, Any] = {
            "plan_key": parsed.plan_key,
            "ndc": ndc,
            "ytd_oop_spend": parsed.ytd_oop_spend or 0.0,
            "ytd_oop_spend_provided": parsed.ytd_provided,
        }
        if parsed.quantity is not None:
            form_args["quantity"] = parsed.quantity
        return ChatWithToolsResult(
            content=None,
            tool_calls=[_tool_call("formulary_benefit_lookup", form_args)],
        )

    form = _tool_result(messages, "formulary_benefit_lookup")

    if parsed.wants_trend and rxcui and "cost_trend_lookup" not in done:
        return ChatWithToolsResult(
            content=None,
            tool_calls=[_tool_call("cost_trend_lookup", {"rxcui": rxcui})],
        )

    if parsed.wants_alternatives and rxcui and "alternatives_finder" not in done:
        return ChatWithToolsResult(
            content=None,
            tool_calls=[_tool_call("alternatives_finder", {"rxcui": rxcui})],
        )

    trend = _tool_result(messages, "cost_trend_lookup")
    alts = _tool_result(messages, "alternatives_finder")

    if (parsed.wants_policy or parsed.wants_trend) and "policy_retrieval" not in done:
        query = f"Explain cost factors for {drug_name} message={message}"
        return ChatWithToolsResult(
            content=None,
            tool_calls=[_tool_call("policy_retrieval", {"query_text": query})],
        )

    policy = _tool_result(messages, "policy_retrieval")
    explanation = _build_final_explanation(
        message, parsed, norm, form, trend, alts, policy
    )
    return ChatWithToolsResult(content=explanation)
