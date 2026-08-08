from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from medicare_navigator.llm.types import ChatWithToolsResult, ToolCallSpec
from medicare_navigator.tools.pharmacy_channels import channel_cost_bounds

T = TypeVar("T", bound=BaseModel)

_PLAN_KEY_RE = re.compile(r"^[A-Za-z]\d{4}-\d{3}$", re.I)
_PLAN_BENEFIT_PATTERNS = (
    r"\bmax(?:imum)?\s+(?:out[- ]of[- ]pocket|oop)\b",
    r"\bmoop\b",
    r"\bin[- ]network\b.*\bout[- ]of[- ]network\b",
    r"\bout[- ]of[- ]network\b.*\bin[- ]network\b",
)


def _is_plan_benefit_question(message: str) -> bool:
    text = message.lower()
    return any(re.search(pattern, text, re.I) for pattern in _PLAN_BENEFIT_PATTERNS)


def _is_plan_key_token(token: str) -> bool:
    return bool(_PLAN_KEY_RE.match(token.strip()))


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
        "comparison",
        "network",
        "maximum",
        "max",
        "oop",
        "compare",
        "between",
        "would",
        "will",
        "much",
        "on",
        "plan",
        "medication",
        "medications",
        "today",
        "start",
        "taking",
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
    tokens = sorted(re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", message), key=len, reverse=True)
    for token in tokens:
        lower = token.lower()
        if lower not in _QUESTION_WORDS and not _is_plan_key_token(token):
            return lower

    for_match = re.search(r"\bfor\s+([a-zA-Z][a-zA-Z0-9-]+)", message, re.I)
    if for_match:
        candidate = for_match.group(1)
        if candidate.lower() not in _QUESTION_WORDS and not _is_plan_key_token(candidate):
            return candidate.lower()
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

    if _is_plan_benefit_question(message):
        parsed.drug = None
    elif parsed.plan_key and parsed.drug:
        if parsed.drug.upper() == parsed.plan_key:
            parsed.drug = None

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
            if "Current user message:" in content:
                return _current_message(content.strip())
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = (block.get("text") or "").strip()
                    if text and "Current user message:" in text:
                        return _current_message(text)

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
            # OpenAI-format tool result message: {"role": "tool", "tool_call_id": ..., "content": <json str>}
            if id_to_name.get(msg.get("tool_call_id", "")) != tool_name:
                continue
            payload = msg.get("content")
            if isinstance(payload, str):
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    return None
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


def _plan_keys_from_message(message: str) -> list[str]:
    matches = re.findall(r"\b([A-Za-z]\d{4}-\d{3})\b", message, re.I)
    seen: list[str] = []
    for m in matches:
        upper = m.upper()
        if upper not in seen:
            seen.append(upper)
    return seen


_MULTI_DRUG_SEGMENT_RE = re.compile(
    r"(?:cost(?:s)?\s+for|estimate(?:s)?\s+for)\s+(.+?)\s+(?:on\s+plan|for\s+plan)",
    re.I,
)


def _drug_fragments_from_message(message: str) -> list[str] | None:
    """Detect a 'DRUG1[, DRUG2...] and DRUGN' list in a 'cost for ... on plan' clause, for
    mock multi-drug-basket support. Returns None unless 2+ items are present."""
    match = _MULTI_DRUG_SEGMENT_RE.search(message)
    if not match:
        return None
    segment = match.group(1)
    parts = [p.strip() for p in re.split(r"\s*,\s*|\s+and\s+", segment) if p.strip()]
    return parts if len(parts) >= 2 else None


def _parse_drug_fragment(fragment: str) -> tuple[str | None, str | None]:
    dose_match = re.search(r"(\d+)\s*mg", fragment, re.I)
    dosage = f"{dose_match.group(1)}mg" if dose_match else None
    name_part = re.sub(r"\d+\s*mg", "", fragment, flags=re.I).strip()
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", name_part)
    drug = tokens[0].lower() if tokens else None
    return drug, dosage


def _is_plan_comparison_question(message: str) -> bool:
    return bool(re.search(r"\bcompar", message, re.I)) and len(_plan_keys_from_message(message)) >= 2


def _prior_tool_call_args(messages: list[dict[str, Any]], tool_name: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == tool_name:
                    calls.append(block.get("input") or {})
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            if fn.get("name") == tool_name:
                try:
                    calls.append(json.loads(fn.get("arguments") or "{}"))
                except json.JSONDecodeError:
                    calls.append({})
    return calls


def _prior_tool_results(messages: list[dict[str, Any]], tool_name: str) -> list[dict[str, Any]]:
    id_to_name = _tool_use_ids(messages)
    results: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "tool":
            if id_to_name.get(msg.get("tool_call_id", "")) != tool_name:
                continue
            payload = msg.get("content")
            if isinstance(payload, str):
                try:
                    results.append(json.loads(payload))
                except json.JSONDecodeError:
                    pass
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            if id_to_name.get(block.get("tool_use_id", "")) != tool_name:
                continue
            payload = block.get("content")
            if isinstance(payload, str):
                try:
                    results.append(json.loads(payload))
                except json.JSONDecodeError:
                    pass
    return results


def _build_multi_drug_explanation(results: list[dict[str, Any]]) -> str:
    return "\n\n".join(_build_final_explanation(estimate) for estimate in results)


def _build_plan_comparison_explanation(results: list[dict[str, Any]]) -> str:
    parts = [_build_final_explanation(estimate) for estimate in results]
    parts.append(
        "Plan premiums are not included in this comparison — only this fill's pharmacy "
        "cost-share. This is not a recommendation to switch plans."
    )
    return "\n\n".join(parts)


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
    channels = data.get("channels")
    if channels:
        cost_low, cost_high = channel_cost_bounds(channels)
    else:
        cost_low = data.get("cost_low")
        cost_high = data.get("cost_high")

    parts: list[str] = []
    if cost_low is not None and cost_high is not None:
        cost_text = (
            f"${cost_low:.2f}" if cost_low == cost_high else f"${cost_low:.2f}–${cost_high:.2f}"
        )
        channel_note = " depending on pharmacy channel" if channels else ""
        parts.append(
            f"{drug_name.capitalize()} for a {days_supply}-day supply on {plan_name} is "
            f"estimated at {cost_text}{channel_note} ({phase} phase)."
        )
    else:
        parts.append(
            f"I could not compute a dollar estimate for {drug_name} on {plan_name}; see the "
            "notes below."
        )

    for caveat in data.get("caveats") or []:
        parts.append(caveat)

    return "\n\n".join(parts)


def _build_plan_benefit_scope_refusal(
    lookup: dict[str, Any] | None,
    plan_key: str | None,
) -> str:
    plan_label = plan_key or "that plan"
    if lookup and lookup.get("status") == "ok":
        plan = (lookup.get("data") or {}).get("plan") or {}
        if plan.get("plan_name"):
            plan_label = f"{plan['plan_name']} ({plan.get('plan_key', plan_key)})"
    elif lookup and lookup.get("status") == "not_found" and plan_key:
        plan_label = plan_key

    parts = [
        f"I looked up {plan_label}. This Navigator estimates per-prescription fill costs from "
        "CMS SPUF formulary data — it does not include Medicare Advantage in-network or "
        "out-of-network maximum out-of-pocket (MOOP) limits.",
        "Those MOOP figures come from separate CMS plan-benefit data, not the Part D formulary "
        "files we use.",
    ]
    if lookup and lookup.get("status") == "ok":
        deductible = (lookup.get("data") or {}).get("plan", {}).get("deductible")
        if deductible is not None:
            parts.append(
                f"From SPUF I can see this plan's Part D deductible is ${deductible:.2f}; "
                "that is not the same as in-network vs out-of-network MOOP."
            )
    parts.append(
        "If you tell me a specific drug, I can estimate that fill's cost on this plan."
    )
    return "\n\n".join(parts)


async def mock_chat_with_tools(
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    model: str = "gpt-5.4-nano",
) -> ChatWithToolsResult:
    from medicare_navigator.llm.types import TokenUsage

    message = _extract_user_message(messages)
    parsed = parse_message(message)
    done = _tools_done(messages)

    def _mock_usage(content: str | None) -> TokenUsage:
        chars_in = len(system_prompt) + sum(len(str(m)) for m in messages)
        chars_out = len(content or "")
        return TokenUsage(input_tokens=max(chars_in // 4, 1), output_tokens=max(chars_out // 4, 1))

    if _is_plan_benefit_question(message):
        if parsed.plan_key and "lookup_plan" not in done:
            return ChatWithToolsResult(
                content=None,
                tool_calls=[_tool_call("lookup_plan", {"plan_key": parsed.plan_key})],
                usage=_mock_usage(None),
            )
        if "lookup_plan" in done or not parsed.plan_key:
            lookup = _tool_result(messages, "lookup_plan") if "lookup_plan" in done else None
            content = _build_plan_benefit_scope_refusal(lookup, parsed.plan_key)
            return ChatWithToolsResult(content=content, usage=_mock_usage(content))

    plan_keys = _plan_keys_from_message(message)
    drug_fragments = _drug_fragments_from_message(message)

    if drug_fragments and len(plan_keys) == 1:
        plan_key = plan_keys[0]
        fragments = [_parse_drug_fragment(f) for f in drug_fragments]
        fragments = [(d, dos) for d, dos in fragments if d]
        prior_calls = _prior_tool_call_args(messages, "estimate_drug_cost_all_channels")
        called = {(c.get("plan_key"), (c.get("drug_name") or "").lower()) for c in prior_calls}
        pending = [(d, dos) for d, dos in fragments if (plan_key, d) not in called]
        if pending:
            calls = []
            for drug, dosage in pending:
                args: dict[str, Any] = {
                    "plan_key": plan_key,
                    "drug_name": drug,
                    "ytd_oop_spend": parsed.ytd_oop_spend or 0.0,
                }
                if dosage:
                    args["dosage"] = dosage
                if parsed.days_supply:
                    args["days_supply"] = parsed.days_supply
                calls.append(_tool_call("estimate_drug_cost_all_channels", args))
            return ChatWithToolsResult(content=None, tool_calls=calls, usage=_mock_usage(None))
        results = _prior_tool_results(messages, "estimate_drug_cost_all_channels")
        content = _build_multi_drug_explanation(results)
        return ChatWithToolsResult(content=content, usage=_mock_usage(content))

    if _is_plan_comparison_question(message) and parsed.drug and len(plan_keys) >= 2:
        prior_calls = _prior_tool_call_args(messages, "estimate_drug_cost_all_channels")
        called_plans = {c.get("plan_key") for c in prior_calls}
        pending_plans = [p for p in plan_keys if p not in called_plans]
        if pending_plans:
            calls = []
            for plan_key in pending_plans:
                args: dict[str, Any] = {
                    "plan_key": plan_key,
                    "drug_name": parsed.drug,
                    "ytd_oop_spend": parsed.ytd_oop_spend or 0.0,
                }
                if parsed.dosage:
                    args["dosage"] = parsed.dosage
                if parsed.days_supply:
                    args["days_supply"] = parsed.days_supply
                calls.append(_tool_call("estimate_drug_cost_all_channels", args))
            return ChatWithToolsResult(content=None, tool_calls=calls, usage=_mock_usage(None))
        results = _prior_tool_results(messages, "estimate_drug_cost_all_channels")
        content = _build_plan_comparison_explanation(results)
        return ChatWithToolsResult(content=content, usage=_mock_usage(content))

    if not parsed.drug:
        content = (
            "Which drug would you like a cost estimate for? I can look up formulary tier "
            "and cost-sharing once you name the medication."
        )
        return ChatWithToolsResult(content=content, usage=_mock_usage(content))

    if not parsed.plan_key:
        content = (
            f"I found {parsed.drug}. Which Medicare plan should I check "
            "(for example, plan S5678-012)?"
        )
        return ChatWithToolsResult(content=content, usage=_mock_usage(content))

    if "estimate_drug_cost_all_channels" not in done and "estimate_drug_cost" not in done:
        args: dict[str, Any] = {
            "plan_key": parsed.plan_key,
            "drug_name": parsed.drug,
            "ytd_oop_spend": parsed.ytd_oop_spend or 0.0,
        }
        if parsed.dosage:
            args["dosage"] = parsed.dosage
        if parsed.days_supply:
            args["days_supply"] = parsed.days_supply
        return ChatWithToolsResult(
            content=None,
            tool_calls=[_tool_call("estimate_drug_cost_all_channels", args)],
            usage=_mock_usage(None),
        )

    estimate = _tool_result(messages, "estimate_drug_cost_all_channels") or _tool_result(
        messages, "estimate_drug_cost"
    )
    content = _build_final_explanation(estimate)
    return ChatWithToolsResult(content=content, usage=_mock_usage(content))


def mock_structured_completion(
    user_prompt: str,
    response_model: type[T],
    agent_name: str = "agent",
) -> T:
    return response_model.model_validate({})
