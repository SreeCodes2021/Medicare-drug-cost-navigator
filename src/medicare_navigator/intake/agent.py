from __future__ import annotations

import re

from pydantic import BaseModel, Field

from medicare_navigator.intake.merger import InputMerger
from medicare_navigator.llm.client import llm_client
from medicare_navigator.models.query import IntakeResult, QuerySlots
from medicare_navigator.storage.repository import PlanRepository
from medicare_navigator.tools.normalize_drug import normalize_drug

INTAKE_SYSTEM_PROMPT = """You are the Intake agent for a Medicare drug cost navigator.
Extract structured slots from the user's message: drug, dosage, plan_id (format HXXXX-YYY),
ytd_oop_spend, and intents (tier_lookup, explain_cost_change, alternatives).
When the current message is a follow-up, preserve context from recent conversation and
only update slots explicitly mentioned in the current message.
Never guess drug names. If ambiguous, leave fields null.
Never recommend switching plans. Never give medical advice."""

_FOLLOW_UP_COUNT_RE = re.compile(
    r"only one|how many alternative|did you find|just one|any other alternative|is that all",
    re.I,
)


class IntakeLLMOutput(BaseModel):
    drug: str | None = None
    dosage: str | None = None
    plan_id: str | None = None
    ytd_oop_spend: float | None = None
    intents: list[str] = Field(default_factory=lambda: ["tier_lookup"])
    confidence: float = 0.0
    is_follow_up: bool = False
    follow_up_type: str | None = None


def detect_follow_up_type(message: str, chat_history: list[dict] | None) -> str | None:
    if not chat_history:
        return None
    if _FOLLOW_UP_COUNT_RE.search(message):
        return "clarify_count"
    return None


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


def _build_intake_prompt(message: str, chat_history: list[dict] | None) -> str:
    history_block = _format_chat_history(chat_history)
    parts = []
    if history_block:
        parts.append(history_block)
        parts.append("")
    parts.append(f"Current message: {message}")
    parts.append("Extract slots from the current message. Treat it as a follow-up when it references prior results.")
    return "\n".join(parts)


async def run_intake(
    message: str,
    filter_slots: QuerySlots | None = None,
    session_slots: QuerySlots | None = None,
    chat_history: list[dict] | None = None,
) -> IntakeResult:
    follow_up_type = detect_follow_up_type(message, chat_history)

    llm_out = await llm_client.structured_completion(
        system_prompt=INTAKE_SYSTEM_PROMPT,
        user_prompt=_build_intake_prompt(message, chat_history),
        response_model=IntakeLLMOutput,
        agent_name="intake",
    )

    follow_up_type = llm_out.follow_up_type or follow_up_type

    ytd = llm_out.ytd_oop_spend
    spend_mentioned = bool(
        re.search(r"spent\s+\$?\s*\d", message, re.I)
        or re.search(r"\$\d+(?:\.\d+)?\s+ytd", message, re.I)
        or re.search(r"what if i'?ve spent", message, re.I)
    )
    if ytd == 0.0 and not spend_mentioned:
        ytd = None

    chat_slots = QuerySlots(
        drug=llm_out.drug,
        dosage=llm_out.dosage,
        plan_id=llm_out.plan_id,
        ytd_oop_spend=ytd,
        intents=llm_out.intents,
        raw_message=message,
    )

    if not chat_slots.plan_id:
        plan_match = re.search(r"plan\s+([A-Za-z0-9]+-\d{3})", message, re.I)
        if plan_match:
            chat_slots.plan_id = plan_match.group(1).upper()
        for token in message.split():
            matches = PlanRepository().fuzzy_match_plan(token)
            if matches:
                chat_slots.plan_id = matches[0]["plan_key"]
                break
        if not chat_slots.plan_id:
            matches = PlanRepository().fuzzy_match_plan(message)
            if len(matches) == 1:
                chat_slots.plan_id = matches[0]["plan_key"]

    merged = InputMerger.merge(chat_slots, filter_slots, session_slots, raw_message=message)
    slots_unchanged = InputMerger.slots_unchanged(session_slots, merged)

    if follow_up_type == "clarify_count" and "alternatives" not in (merged.intents or []):
        merged.intents = sorted(set(merged.intents or []) | {"alternatives"})

    if merged.ytd_oop_spend is None:
        spend_patterns = [
            r"spent\s+\$?\s*(\d+(?:\.\d+)?)",
            r"\$(\d+(?:\.\d+)?)\s+ytd",
            r"what if i'?ve spent\s+\$?\s*(\d+(?:\.\d+)?)",
        ]
        for pattern in spend_patterns:
            m = re.search(pattern, message, re.I)
            if m:
                merged.ytd_oop_spend = float(m.group(1))
                slots_unchanged = False
                break

    if not merged.drug:
        return IntakeResult(
            status="needs_clarification",
            slots=merged,
            clarification_message="Which drug would you like to look up? Please provide the drug name and dosage.",
            missing_slots=["drug"],
            follow_up_type=follow_up_type,
            slots_unchanged=slots_unchanged,
        )

    norm_result = await normalize_drug(merged.drug, merged.dosage)
    if norm_result.status.value == "not_found":
        return IntakeResult(
            status="not_found",
            slots=merged,
            clarification_message=norm_result.message or f"I couldn't find '{merged.drug}'. Please check the spelling.",
            follow_up_type=follow_up_type,
            slots_unchanged=slots_unchanged,
        )

    selected = norm_result.data["selected"]
    plan_required_intents = {"tier_lookup", "explain_cost_change"}
    needs_plan = bool(plan_required_intents.intersection(set(merged.intents or []))) or (
        "tier" in message.lower() or "copay" in message.lower() or "cost" in message.lower()
    )
    if not merged.plan_id and needs_plan:
        return IntakeResult(
            status="needs_clarification",
            slots=merged,
            clarification_message=(
                f"I found {selected['drug_name']} {selected.get('dosage', '')}. "
                "Which plan should I check? Provide a plan ID like H1234-045 or a plan name."
            ),
            missing_slots=["plan_id"],
            follow_up_type=follow_up_type,
            slots_unchanged=slots_unchanged,
        )

    if merged.plan_id:
        plan = PlanRepository().get_plan(merged.plan_id)
        if not plan:
            return IntakeResult(
                status="not_found",
                slots=merged,
                clarification_message=(
                    f"Plan '{merged.plan_id}' was not found in the demo plan set. "
                    "Please provide a valid plan ID like H1234-045."
                ),
                follow_up_type=follow_up_type,
                slots_unchanged=slots_unchanged,
            )

    parsed = InputMerger.to_parsed_query(merged, selected)
    return IntakeResult(
        status="complete",
        slots=merged,
        parsed_query=parsed,
        follow_up_type=follow_up_type,
        slots_unchanged=slots_unchanged,
    )
