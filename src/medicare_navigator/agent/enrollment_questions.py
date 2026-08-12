"""Early-return refusals for enrollment / sign-up requests — before plan lookup or estimates."""

from __future__ import annotations

import re

_ENROLLMENT_RE = re.compile(
    r"\b(?:enroll(?:\s+me|\s+in|\s+for)?|sign\s+me\s+up|register\s+(?:me\s+)?(?:for|in))\b",
    re.I,
)


def build_enrollment_refusal_explanation() -> str:
    return (
        "I can't help with Medicare enrollment or plan sign-up. For enrollment, contact "
        "Medicare at 1-800-MEDICARE (1-800-633-4227) or visit medicare.gov. I can estimate "
        "prescription drug costs for a specific drug, strength, and plan when you name them."
    )


def resolve_enrollment_question(
    message: str,
) -> tuple[str, dict[str, object], list[str]] | None:
    """Return (explanation, tool_artifacts, tools_invoked) or None to defer to the LLM."""
    if not _ENROLLMENT_RE.search(message):
        return None
    return build_enrollment_refusal_explanation(), {}, []
