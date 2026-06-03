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
        "cost",
        "costs",
        "the",
        "for",
        "and",
        "only",
        "find",
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
        "supply",
        "days",
        "day",
    }
)


@dataclass
class ParsedMessage:
    drug: str | None = None
    dosage: str | None = None
    plan_key: str | None = None
    ytd_oop_spend: float | None = None
    ytd_provided: bool = False
    days_supply: int | None = None


def _current_message(user_prompt: str) -> str:
    if "Current user message:" in user_prompt:
        return user_prompt.split("Current user message:", 1)[-1].strip()
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

    days_match = re.search(r"(\d+)[\s-]*day", text)
    if days_match:
        parsed.days_supply = int(days_match.group(1))

    return parsed


def _extract_user_message(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return _current_message(content.strip())
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = (block.get("text") or "").strip()
                    if text:
                        return _current_message(text)
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


def _build_final_explanation(estimate: dict[str, Any] | None) -> str:
    if not estimate:
        return "I retrieved data for your query but could not build a supported summary."

    status = estimate.get("status")
    message = estimate.get("message")
    data = estimate.get("data") or {}

    if status in ("suppressed", "insulin_out_of_scope", "quantity_limit_blocked"):
        return message or "This request is out of scope."

    if status == "not_covered":
        return message or "This drug does not appear to be covered on this plan's formulary."

    if status not in ("ok",):
        return message or "I could not find the information needed to answer that."

    drug_name = data.get("drug_name", "This drug")
    plan_name = data.get("plan_name", "this plan")
    days_supply = data.get("days_supply", 30)
    phase = (data.get("benefit_phase") or "").replace("_", " ")
    cost_low = data.get("cost_low")
    cost_high = data.get("cost_high")

    parts: list[str] = []
    if cost_low is not None and cost_high is not None:
        cost_text = (
            f"${cost_low:.2f}" if cost_low == cost_high else f"${cost_low:.2f}–${cost_high:.2f}"
        )
        parts.append(
            f"{drug_name.capitalize()} for a {days_supply}-day supply on {plan_name} is "
            f"estimated at {cost_text} ({phase} phase)."
        )
    else:
        parts.append(
            f"I could not compute a dollar estimate for {drug_name} on {plan_name}; see the "
            "notes below."
        )

    for caveat in data.get("caveats") or []:
        parts.append(caveat)

    return "\n\n".join(parts)


async def mock_chat_with_tools(
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> ChatWithToolsResult:
    message = _extract_user_message(messages)
    parsed = parse_message(message)
    done = _tools_done(messages)

    if not parsed.drug:
        return ChatWithToolsResult(
            content=(
                "Which drug would you like a cost estimate for? I can look up formulary tier "
                "and cost-sharing once you name the medication."
            )
        )

    if not parsed.plan_key:
        return ChatWithToolsResult(
            content=(
                f"I found {parsed.drug}. Which Medicare plan should I check "
                "(for example, plan S5678-012)?"
            )
        )

    if "estimate_drug_cost" not in done:
        args: dict[str, Any] = {
            "plan_key": parsed.plan_key,
            "drug_name": parsed.drug,
            "ytd_oop_spend": parsed.ytd_oop_spend or 0.0,
        }
        if parsed.dosage:
            args["dosage"] = parsed.dosage
        if parsed.days_supply:
            args["days_supply"] = parsed.days_supply
        return ChatWithToolsResult(content=None, tool_calls=[_tool_call("estimate_drug_cost", args)])

    estimate = _tool_result(messages, "estimate_drug_cost")
    return ChatWithToolsResult(content=_build_final_explanation(estimate))


def mock_structured_completion(
    user_prompt: str,
    response_model: type[T],
    agent_name: str = "agent",
) -> T:
    return response_model.model_validate({})
