from __future__ import annotations

import json
import re

from pydantic import BaseModel

from medicare_navigator.llm.client import llm_client
from medicare_navigator.models.query import IntakeResult

CLARIFICATION_SYSTEM_PROMPT = """You are the Clarification agent for a Medicare drug cost navigator.
The user asked a question but we need more information before checking formulary eligibility.
Ask ONE short, conversational question for what is missing.

Rules:
- Only suggest drug names from drug_candidates or resolved_drug — never invent drugs.
- Never state tier, copay, coverage, or eligibility — we have not looked up the formulary yet.
- If the drug is resolved but plan is missing, confirm the drug and ask which plan to check.
- If drug candidates exist, suggest them (e.g. "Did you mean omeprazole?").
- Never recommend switching plans. Never give medical advice."""


class ClarificationLLMOutput(BaseModel):
    message: str


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


def _format_candidate(candidate: dict | None) -> str:
    if not candidate:
        return ""
    name = candidate.get("drug_name") or candidate.get("name", "")
    dosage = candidate.get("dosage")
    if dosage:
        return f"{name} {dosage}".strip()
    return name


def _deterministic_clarification(intake: IntakeResult) -> str:
    resolved = intake.resolved_drug
    candidates = intake.drug_candidates
    drug_query = intake.slots.drug

    if resolved and "plan_id" in intake.missing_slots:
        label = _format_candidate(resolved)
        return (
            f"I found {label}. To check eligibility, which plan should I look up? "
            "You can provide a plan ID like H1234-045 or a plan name."
        )

    if candidates and intake.status == "not_found":
        names = ", ".join(_format_candidate(c) for c in candidates[:3])
        return (
            f"I couldn't find an exact match for '{drug_query}'. "
            f"Did you mean {names}? Please confirm the drug name and dosage."
        )

    if "drug" in intake.missing_slots:
        return "Which drug would you like to look up? Please provide the drug name and dosage."

    if intake.status == "not_found" and drug_query:
        return f"I couldn't find '{drug_query}'. Please check the spelling and include the dosage."

    if intake.slots.plan_id and intake.status == "not_found":
        return (
            f"Plan '{intake.slots.plan_id}' was not found. "
            "Please provide a valid plan ID like H1234-045."
        )

    return "Could you share a bit more detail so I can look that up?"


def _build_context(intake: IntakeResult, message: str) -> str:
    payload = {
        "user_message": message,
        "status": intake.status,
        "missing_slots": intake.missing_slots,
        "slots": intake.slots.model_dump(exclude_none=True),
        "resolved_drug": intake.resolved_drug,
        "drug_candidates": intake.drug_candidates,
    }
    return json.dumps(payload, indent=2)


async def run_clarification_agent(
    message: str,
    intake: IntakeResult,
    chat_history: list[dict] | None = None,
) -> tuple[str, str]:
    fallback = _deterministic_clarification(intake)

    history_block = _format_chat_history(chat_history)
    user_prompt_parts = []
    if history_block:
        user_prompt_parts.append(history_block)
    user_prompt_parts.append(f"Context:\n{_build_context(intake, message)}")

    llm_out = await llm_client.structured_completion(
        system_prompt=CLARIFICATION_SYSTEM_PROMPT,
        user_prompt="\n\n".join(user_prompt_parts),
        response_model=ClarificationLLMOutput,
        agent_name="clarification",
    )

    text = (llm_out.message or "").strip()
    if not text:
        return fallback, llm_client.model_label()

    lowered = text.lower()
    if any(word in lowered for word in ("tier", "copay", "covered", "not covered", "eligible")):
        return fallback, llm_client.model_label()

    allowed_names = {
        (_format_candidate(c) or "").lower()
        for c in intake.drug_candidates
    }
    if intake.resolved_drug:
        allowed_names.add((_format_candidate(intake.resolved_drug) or "").lower())
    allowed_names.discard("")
    if allowed_names:
        invented = re.findall(r"did you mean ([a-z0-9 -]+)\??", lowered)
        for guess in invented:
            if guess.strip() not in allowed_names:
                return fallback, llm_client.model_label()

    return text, llm_client.model_label()
