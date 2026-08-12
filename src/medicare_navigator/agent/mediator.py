"""Stateless request-normalization layer, run once per turn before the deterministic
resolvers and the main agent loop.

The mediator does not answer questions, calculate costs, decide routing, or hold
conversation state across turns — it only rewrites the raw message into the vocabulary the
existing resolvers already understand, and extracts date/duration facts as raw components
(never a computed date; see MediatorRewrite). Safety-critical refusal checks in
Navigator.run always run on the original raw message, never on this rewrite.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel

from medicare_navigator.agent.datetime_context import build_datetime_context
from medicare_navigator.config import settings
from medicare_navigator.llm.client import llm_client
from medicare_navigator.llm.models import default_mediator_llm_model
from medicare_navigator.llm.types import TokenUsage

logger = logging.getLogger(__name__)


class MediatorRewrite(BaseModel):
    normalized_message: str
    # Date/duration components only — never a computed date or resulting end date. The
    # window arithmetic (add_months, year inference) always happens in deterministic code,
    # not here — see agent/datetime_context.py.
    duration_count: int | None = None
    duration_unit: Literal["days", "weeks", "months", "years"] | None = None
    anchor_today: bool = False
    explicit_month: int | None = None
    explicit_day: int | None = None
    explicit_year: int | None = None


_MEDIATOR_SYSTEM_PROMPT = """You are a request-normalizer for a Medicare drug-cost app. You do not answer questions,
calculate costs, or give advice — only rewrite text and extract date/duration facts.

Rules:
1. Never invent or guess a drug name, plan ID, dosage, dollar amount, or date. If something
   is not explicitly stated in the message or in the context below, leave it out.
2. If the message already states something unambiguously (an exact plan ID like H0270-001,
   an exact dollar figure, an exact drug name), copy it through verbatim. Do not paraphrase
   or "improve" anything that was already clear.
3. Strip conversational filler (greetings, "um", "hey what's up") — it carries no signal.
4. Fix obvious typos in ordinary English words (e.g. "durg" -> "drug", "sup" -> "what's up").
   Never "fix" a word into a specific drug or plan name inferred from context — a typo'd
   generic word stays generic.
5. Extract duration/date information only as the structured fields below — never state or
   compute a resulting date yourself.
6. A fill size ("30-day supply", "90 days supply") is NOT a date-range duration — do not
   populate duration_count/duration_unit for a days-supply phrase. Only populate them for an
   actual date-range/budget window request ("the next 4 months", "for 6 weeks").
"""


def _format_last_tool_call(last_tool_call: dict[str, Any] | None) -> str:
    if not last_tool_call:
        return "none"
    args = last_tool_call.get("arguments") or {}
    parts = [f"{key}={value}" for key, value in args.items() if value is not None]
    return ", ".join(parts) if parts else "none"


def _format_pending_clarification(pending_clarification: dict[str, Any] | None) -> str:
    if not pending_clarification:
        return "none"
    drugs = pending_clarification.get("drugs") or []
    return f"waiting on strength for: {', '.join(drugs)}" if drugs else "none"


def _build_user_prompt(
    message: str,
    *,
    last_tool_call: dict[str, Any] | None,
    pending_clarification: dict[str, Any] | None,
    timezone: str | None,
) -> str:
    return (
        f"{build_datetime_context(timezone)}\n\n"
        "Context (for resolving pronouns/follow-ups only — not a source of new facts):\n"
        f"Last cost estimate call this session: {_format_last_tool_call(last_tool_call)}\n"
        f"Pending clarification: {_format_pending_clarification(pending_clarification)}\n\n"
        f"Current user message: {message}"
    )


async def rewrite_and_extract(
    message: str,
    *,
    last_tool_call: dict[str, Any] | None = None,
    pending_clarification: dict[str, Any] | None = None,
    model: str | None = None,
    timezone: str | None = None,
) -> tuple[MediatorRewrite | None, TokenUsage, str]:
    """Returns (result or None on any failure, usage, model_id used).

    Never raises — every failure path (timeout, API error, validation error, empty output)
    is caught here and returns None; the caller's fallback is always "use the raw/spliced
    message unchanged," never a hang or a propagated exception.
    """
    resolved_model = model or settings.mediator_llm_model or default_mediator_llm_model()
    user_prompt = _build_user_prompt(
        message,
        last_tool_call=last_tool_call,
        pending_clarification=pending_clarification,
        timezone=timezone,
    )
    try:
        result, usage = await llm_client.structured_complete(
            _MEDIATOR_SYSTEM_PROMPT,
            user_prompt,
            MediatorRewrite,
            model=resolved_model,
        )
    except Exception as exc:  # noqa: BLE001 - any failure here means "fall back to raw text"
        logger.info("mediator_failed reason=%s", exc)
        return None, TokenUsage(), resolved_model

    if not result.normalized_message.strip():
        logger.info("mediator_failed reason=empty_output")
        return None, TokenUsage(), resolved_model

    return result, usage, resolved_model
