"""Early-return refusals for clinical / therapeutic questions — before dosage clarification."""

from __future__ import annotations

import re

from medicare_navigator.tools.drug_lookup import COMMON_DRUGS

_SHOULD_I_MEDICAL_RE = re.compile(
    r"\bshould\s+i\s+(?:switch|change|stop|start|take|use)\b",
    re.I,
)
_SWITCH_FROM_TO_RE = re.compile(
    r"\b(?:switch|change)\s+from\s+.+\s+to\s+",
    re.I,
)
_EFFICACY_COMPARE_RE = re.compile(
    r"\b(?:is|are)\s+.+\s+better\s+(?:than|for)\b",
    re.I,
)
_SAFETY_QUESTION_RE = re.compile(
    r"\b(?:is|are)\s+.+\s+safe\b"
    r"|\bsafe\s+(?:to\s+take|during|while|for)\b"
    r"|\b(?:side\s+effects?|interact(?:s|ion)?|contraindicat\w*|pregnan\w*|breastfeed\w*|nursing)\b",
    re.I,
)
_CONDITION_CONTEXT_RE = re.compile(
    r"\bfor\s+(?:my\s+)?(?:diabetes|blood\s+pressure|cholesterol|anxiety|depression|pain|heart|arthritis)\b",
    re.I,
)
_COST_INTENT_RE = re.compile(
    r"\b(?:cost|copay|coinsurance|price|tier|formulary|estimate)\b",
    re.I,
)
_COMPARE_COST_RE = re.compile(r"\bcompare\b.+\bcost", re.I)


def _mentions_common_drug(message: str) -> bool:
    lower = message.lower()
    return any(re.search(rf"\b{re.escape(drug)}\b", lower) for drug in COMMON_DRUGS)


def is_medical_advice_question(message: str) -> bool:
    """True when the user asks for clinical judgment, not a CMS cost estimate."""
    if _COST_INTENT_RE.search(message) or _COMPARE_COST_RE.search(message):
        return False
    if _SHOULD_I_MEDICAL_RE.search(message) and (
        _mentions_common_drug(message) or _CONDITION_CONTEXT_RE.search(message)
    ):
        return True
    if _SWITCH_FROM_TO_RE.search(message) and _mentions_common_drug(message):
        return True
    if _EFFICACY_COMPARE_RE.search(message) and (
        _mentions_common_drug(message) or _CONDITION_CONTEXT_RE.search(message)
    ):
        return True
    if _SAFETY_QUESTION_RE.search(message) and (
        _mentions_common_drug(message) or _CONDITION_CONTEXT_RE.search(message)
    ):
        return True
    return False


def build_medical_advice_refusal_explanation() -> str:
    return (
        "I can't give medical advice about whether to switch medications or which drug "
        "is better for a health condition. Please discuss any treatment changes with "
        "your doctor or pharmacist. I can estimate Medicare Part D out-of-pocket costs "
        "for a specific drug, strength, and plan when you name them."
    )


def resolve_medical_advice_question(
    message: str,
) -> tuple[str, dict[str, object], list[str]] | None:
    """Return (explanation, tool_artifacts, tools_invoked) or None to defer to the LLM."""
    if not is_medical_advice_question(message):
        return None
    return build_medical_advice_refusal_explanation(), {}, []
