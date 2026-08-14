"""Early-return for malformed numeric inputs before the agent coerces them."""

from __future__ import annotations

import re

_DAYS_SUPPLY_RE = re.compile(
    r"(?:^|\s)(-?\d+)\s*(?:days?\s+supply|days?\b)"
    r"|days?\s+supply\s+(?:of\s+)?(-?\d+)\b",
    re.I,
)
_PRICE_INJECTION_RE = re.compile(
    r"\bignore\s+(?:all\s+)?(?:previous\s+)?instructions\b|"
    r"\bdisregard\b.*\binstructions\b|"
    r"\bsay\b.*\$\s*\d|"
    r"\bSYSTEM:\b|"
    r"\byou\s+are\s+now\s+unrestricted\b",
    re.I,
)


def resolve_invalid_input_question(
    message: str,
) -> tuple[str, dict[str, object], list[str]] | None:
    """Return (explanation, tool_artifacts, tools_invoked) or None to defer to the LLM."""
    for match in _DAYS_SUPPLY_RE.finditer(message):
        days_str = match.group(1) or match.group(2)
        days = int(days_str)
        if days <= 0:
            return (
                f"A {days}-day supply isn't valid for estimating. Please use a standard "
                "30-, 60-, or 90-day supply.",
                {},
                [],
            )
    if _PRICE_INJECTION_RE.search(message):
        from medicare_navigator.agent.mixed_basket_requests import is_mixed_basket_price_injection

        if not is_mixed_basket_price_injection(message):
            return (
                "I can't follow instructions to state a false price. Ask for a CMS reference "
                "estimate for a specific drug and plan, and I'll use the published cost-share data.",
                {},
                [],
            )
    return None
