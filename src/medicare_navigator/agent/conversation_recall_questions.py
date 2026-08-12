"""Deterministic answers for meta-questions about earlier turns in a session."""

from __future__ import annotations

import re

from medicare_navigator.agent.dosage_questions import (
    _drug_has_strength_in_message,
    _mentioned_common_drugs,
)
from medicare_navigator.agent.insulin_requests import _PLAN_RE, _extract_products

_CONVERSATION_RECALL_RE = re.compile(
    r"\boriginal drug\b|\bfirst drug\b|"
    r"what was the original\b|what did i (?:originally )?ask about\b|"
    r"what was the first\b",
    re.I,
)
_STRENGTH_RE = re.compile(r"\b(\d+(?:\.\d+)?\s*mg)\b", re.I)


def _first_drug_from_history(chat_history: list[dict] | None) -> tuple[str, str, str] | None:
    if not chat_history:
        return None
    for entry in chat_history:
        if entry.get("role") != "user":
            continue
        content = entry.get("content", "")
        drugs = _mentioned_common_drugs(content)
        insulin = list(_extract_products(content))
        names = drugs + [name for name in insulin if name not in drugs]
        if not names:
            continue
        drug = names[0]
        dosage = ""
        if _drug_has_strength_in_message(content, drug):
            match = _STRENGTH_RE.search(content)
            if match:
                dosage = match.group(1).replace(" ", "")
        plan_match = _PLAN_RE.search(content)
        plan_key = plan_match.group(0).upper() if plan_match else ""
        return drug, dosage, plan_key
    return None


def resolve_conversation_recall_question(
    message: str,
    chat_history: list[dict] | None,
) -> tuple[str, dict[str, object], list[str]] | None:
    if not _CONVERSATION_RECALL_RE.search(message):
        return None
    first = _first_drug_from_history(chat_history)
    if first is None:
        return (
            "I don't have an earlier drug question in this session yet.",
            {},
            [],
        )
    drug, dosage, plan_key = first
    parts = [f"You first asked about **{drug}**"]
    if dosage:
        parts.append(f"at {dosage}")
    if plan_key:
        parts.append(f"on plan {plan_key}")
    return (" ".join(parts) + ".", {}, [])
