"""Early-return for malformed numeric inputs before the agent coerces them."""

from __future__ import annotations

import re

_DAYS_SUPPLY_RE = re.compile(
    r"(?:^|\s)(-?\d+)\s*(?:days?\s+supply|days?\b)",
    re.I,
)


def resolve_invalid_input_question(
    message: str,
) -> tuple[str, dict[str, object], list[str]] | None:
    """Return (explanation, tool_artifacts, tools_invoked) or None to defer to the LLM."""
    for match in _DAYS_SUPPLY_RE.finditer(message):
        days = int(match.group(1))
        if days <= 0:
            return (
                f"A {days}-day supply isn't valid for estimating. Please use a standard "
                "30-, 60-, or 90-day supply.",
                {},
                [],
            )
    return None
