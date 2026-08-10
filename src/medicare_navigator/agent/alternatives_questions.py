"""Deterministic answers for open-ended therapeutic-alternatives questions."""

from __future__ import annotations

import re

_ALTERNATIVES_PATTERNS = (
    re.compile(r"\balternativ", re.I),
    re.compile(r"\bsubstitut", re.I),
    re.compile(r"\bcheaper\s+generic\b", re.I),
    re.compile(r"\blower[- ]cost\s+(?:drug|medication|option)s?\b", re.I),
    re.compile(r"\b(?:what|which)\s+(?:cheaper|lower[- ]cost)\b", re.I),
)

# User named a specific strength for a drug they want priced — let the agent estimate it.
_NAMED_DRUG_TO_ESTIMATE_RE = re.compile(
    r"\b(?:estimate|cost|price|compare)\b.*\b\d+\s*mg\b",
    re.I,
)


def is_alternatives_question(message: str) -> bool:
    return any(pattern.search(message) for pattern in _ALTERNATIVES_PATTERNS)


def is_open_ended_alternatives_question(message: str) -> bool:
    """True when the user asks for substitute suggestions, not a named-drug cost estimate."""
    if not is_alternatives_question(message):
        return False
    if _NAMED_DRUG_TO_ESTIMATE_RE.search(message):
        return False
    return True


def build_alternatives_deferral_explanation() -> str:
    return (
        "Discuss any substitute with your doctor or pharmacist before changing medications. "
        "I can estimate the out-of-pocket cost for a specific drug and strength on your "
        "Medicare plan when you name the medication and plan — I can't suggest substitute "
        "drug names or compare therapeutic alternatives until you name a drug to price."
    )


def resolve_alternatives_question(
    message: str,
) -> tuple[str, dict[str, object], list[str]] | None:
    """Return (explanation, tool_artifacts, tools_invoked) or None to defer to the LLM."""
    if not is_open_ended_alternatives_question(message):
        return None
    return build_alternatives_deferral_explanation(), {}, []
