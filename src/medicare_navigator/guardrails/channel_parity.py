"""Channel coverage helpers for guardrails and chat-QA grading."""

from __future__ import annotations

import re
from typing import Any

from medicare_navigator.tools.pharmacy_channels import PHARMACY_CHANNEL_LABELS, PHARMACY_CHANNELS

_UNAVAILABLE_PHRASES = (
    "not available",
    "no published cost-share",
    "no matching estimate",
    "could not be computed",
    "no dollar estimate",
    "can't calculate",
    "cannot calculate",
    "can't compute",
    "cannot compute",
    "unable to calculate",
    "unable to compute",
    "no cost range",
    "no numeric estimate",
    "no numeric cost",
    "does not include a matching cost-share",
    "doesn't include a matching cost-share",
    "missing cms pricing",
    "no dollar out-of-pocket",
    "isn't available",
    "is not available",
    "isnt available",
    "no dollar out-of-pocket estimate",
    "no dollar cost estimate",
    "can't produce a dollar",
    "cannot produce a dollar",
    "can’t produce a dollar",
    "can't produce your",
    "cannot produce your",
)


def text_claims_no_estimate(text: str) -> bool:
    lower = text.lower()
    if _DOLLAR_IN_WINDOW_RE.search(text):
        return False
    return any(phrase in lower for phrase in _UNAVAILABLE_PHRASES)
_PLAN_KEY_RE = re.compile(r"\b[A-Za-z]\d{4}-\d{3}\b")
_LOWEST_PHRASE_RE = re.compile(r"\b(?:lowest|cheapest)\b", re.I)
_DOLLAR_IN_WINDOW_RE = re.compile(r"\$\s*\d+(?:\.\d{1,2})?")

_ALL_CHANNELS_PHRASES = (
    "all cms pharmacy channel",
    "all four cms",
    "all pharmacy channel",
    "every pharmacy channel",
    "all channels",
)

_MAIL_RETAIL_CONTRAST_RE = re.compile(
    r"\bmail(?:[- ]order)?\b.*\bretail\b|\bretail\b.*\bmail(?:[- ]order)?\b",
    re.I,
)
_MAIL_CHANNEL_KEYS = ("preferred_mail", "standard_mail")
_RETAIL_CHANNEL_KEYS = ("preferred_retail", "standard_retail")


def channel_has_estimate(channel: dict[str, Any] | None) -> bool:
    if not isinstance(channel, dict):
        return False
    return channel.get("cost_low") is not None or channel.get("cost_high") is not None


def summarize_channels_dict(channels: dict[str, Any] | None) -> dict[str, Any]:
    """Priced vs missing channels for one MultiChannelDrugCostEstimate.channels dict."""
    channels = channels or {}
    priced: list[str] = []
    missing: list[str] = []
    lows: list[float] = []
    highs: list[float] = []
    for name in PHARMACY_CHANNELS:
        data = channels.get(name)
        if channel_has_estimate(data if isinstance(data, dict) else None):
            priced.append(name)
            if isinstance(data, dict):
                low = data.get("cost_low")
                high = data.get("cost_high")
                if low is not None:
                    lows.append(float(low))
                if high is not None:
                    highs.append(float(high))
                elif low is not None:
                    highs.append(float(low))
        else:
            missing.append(name)
    return {
        "priced_channels": priced,
        "missing_channels": missing,
        "aggregate_cost_low": min(lows) if lows else None,
        "aggregate_cost_high": max(highs) if highs else None,
    }


def summarize_channel_coverage(
    channel_estimates: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Per-plan channel pricing coverage for chat-QA graders and guardrails."""
    if not channel_estimates:
        return []

    summaries: list[dict[str, Any]] = []
    for est in channel_estimates:
        if not isinstance(est, dict):
            continue
        coverage = summarize_channels_dict(est.get("channels"))
        summaries.append(
            {
                "plan_key": est.get("plan_key"),
                "plan_name": est.get("plan_name"),
                "tier": est.get("tier"),
                **coverage,
            }
        )
    return summaries


def prose_channel_overclaim_warnings(
    explanation: str,
    channel_coverage: list[dict[str, Any]] | None,
) -> list[str]:
    """Flag prose that claims full channel coverage when tool data has gaps."""
    if not explanation or not channel_coverage:
        return []

    lower = explanation.lower()
    claims_all_channels = any(phrase in lower for phrase in _ALL_CHANNELS_PHRASES)
    warnings: list[str] = []
    for summary in channel_coverage:
        missing = summary.get("missing_channels") or []
        if not missing:
            continue
        plan_key = summary.get("plan_key") or "unknown plan"
        if claims_all_channels:
            warnings.append(
                f"Prose claims all pharmacy channels, but {plan_key} is missing estimates "
                f"for: {', '.join(missing)}."
            )
        elif len(missing) == len(PHARMACY_CHANNELS):
            warnings.append(
                f"Prose may state a fill cost for {plan_key}, but no channel has a numeric estimate."
            )
    return warnings


def channel_coverage_note(channel_coverage: list[dict[str, Any]]) -> str | None:
    """Plain-language note when CMS data lacks estimates for some channels."""
    lines: list[str] = []
    for summary in channel_coverage:
        missing = summary.get("missing_channels") or []
        priced = summary.get("priced_channels") or []
        if not missing:
            continue
        plan_label = summary.get("plan_key") or summary.get("plan_name") or "this plan"
        priced_labels = [PHARMACY_CHANNEL_LABELS.get(ch, ch) for ch in priced]
        missing_labels = [PHARMACY_CHANNEL_LABELS.get(ch, ch) for ch in missing]
        if priced_labels:
            priced_text = ", ".join(priced_labels)
            missing_text = ", ".join(missing_labels)
            lines.append(
                f"For {plan_label}, CMS published cost-share data is available for "
                f"{priced_text} only; there is no matching estimate for {missing_text}."
            )
        else:
            lines.append(
                f"For {plan_label}, CMS data does not include a matching cost-share row "
                f"for any pharmacy channel at this coverage level."
            )
    if not lines:
        return None
    return " ".join(lines)


def _window_has_dollar_for_aggregate(window: str, agg_low: float | None, agg_high: float | None) -> bool:
    """True when the prose window already states the priced-channel aggregate."""
    if agg_low is None:
        return False
    highs = [agg_low]
    if agg_high is not None:
        highs.append(agg_high)
    for value in highs:
        for fmt in (f"${value:.2f}", f"${value:.1f}", f"${int(value)}", f"${value:g}"):
            if fmt.lower() in window.lower():
                return True
    return bool(_DOLLAR_IN_WINDOW_RE.search(window))


def _plan_lead_window(explanation: str, plan_key: str) -> str:
    """Prose segment for one plan — stops at the next plan_key so neighbor $ figures don't mask gaps."""
    idx = explanation.find(plan_key)
    if idx == -1:
        return ""
    after_key = explanation[idx + len(plan_key) :]
    next_plan = _PLAN_KEY_RE.search(after_key)
    end = next_plan.start() if next_plan else min(len(after_key), 280)
    return explanation[idx : idx + len(plan_key) + end].lower()


def prose_false_unavailable_warnings(
    explanation: str,
    channel_coverage: list[dict[str, Any]] | None,
) -> list[str]:
    """Flag when prose says no estimate exists but tool data has priced channels."""
    if not explanation or not channel_coverage:
        return []

    warnings: list[str] = []
    for summary in channel_coverage:
        priced = summary.get("priced_channels") or []
        if not priced:
            continue
        plan_key = summary.get("plan_key") or ""
        if not plan_key:
            continue
        window = _plan_lead_window(explanation, plan_key)
        if not window:
            continue
        if not any(phrase in window for phrase in _UNAVAILABLE_PHRASES):
            continue
        if _window_has_dollar_for_aggregate(
            window, summary.get("aggregate_cost_low"), summary.get("aggregate_cost_high")
        ):
            continue
        warnings.append(
            f"Prose says no estimate is available for {plan_key}, but CMS data has priced "
            f"channels: {', '.join(priced)} — lead with the dollar figure for those channels."
        )
    return warnings


def prose_tied_lowest_warnings(
    explanation: str,
    channel_coverage: list[dict[str, Any]] | None,
) -> list[str]:
    """Flag when multiple plans tie at the minimum cost but prose names only one as lowest."""
    if not explanation or not channel_coverage or len(channel_coverage) < 2:
        return []
    if not _LOWEST_PHRASE_RE.search(explanation):
        return []

    plan_lows: list[tuple[str, float]] = []
    for summary in channel_coverage:
        plan_key = summary.get("plan_key") or ""
        low = summary.get("aggregate_cost_low")
        if plan_key and low is not None:
            plan_lows.append((plan_key, float(low)))
    if len(plan_lows) < 2:
        return []

    min_low = min(v for _, v in plan_lows)
    tied = [pk for pk, v in plan_lows if v == min_low]
    if len(tied) < 2:
        return []

    # Lowest phrase near exactly one tied plan — other tied plans omitted
    lower_explanation = explanation.lower()
    lowest_idx = _LOWEST_PHRASE_RE.search(lower_explanation)
    if not lowest_idx:
        return []
    vicinity = lower_explanation[max(0, lowest_idx.start() - 20) : lowest_idx.end() + 120]
    tied_in_vicinity = [pk for pk in tied if pk.lower() in vicinity]
    if len(tied_in_vicinity) == 1 and len(tied) > 1:
        omitted = [pk for pk in tied if pk not in tied_in_vicinity]
        return [
            f"Prose names only {tied_in_vicinity[0]} as lowest at ${min_low:.2f}, but "
            f"{', '.join(omitted)} also estimate ${min_low:.2f} — mention the tie or list all."
        ]
    return []


def tier_from_estimate(est: dict[str, Any]) -> int | None:
    tier = est.get("tier")
    if tier is not None:
        return int(tier)
    tiers = est.get("tiers_matched") or []
    if tiers:
        return int(tiers[0])
    return None


def tier_mentioned_in_prose(text: str, tier: int) -> bool:
    return bool(re.search(rf"\btier\s*{tier}\b", text, re.I))


def _is_insulin_cap_estimate(est: dict[str, Any]) -> bool:
    return est.get("benefit_phase") == "insulin_cap" or est.get("effective_phase") == "insulin_cap"


def repair_missing_tier_in_prose(
    explanation: str,
    channel_estimates: list[dict[str, Any]] | None,
) -> str:
    """Prepend grounded tier labels when priced estimates have tier data but prose omits them."""
    if not explanation or not channel_estimates:
        return explanation

    leads: list[str] = []
    seen_tiers: set[int] = set()
    for est in channel_estimates:
        if not isinstance(est, dict) or _is_insulin_cap_estimate(est):
            continue
        tier = tier_from_estimate(est)
        if tier is None or tier in seen_tiers:
            continue
        if tier_mentioned_in_prose(explanation, tier):
            seen_tiers.add(tier)
            continue
        drug = est.get("drug_name") or "This drug"
        leads.append(f"{str(drug).title()} is a Tier {tier} drug on this plan's formulary.")
        seen_tiers.add(tier)

    if not leads:
        return explanation
    return "\n\n".join(leads + [explanation.strip()])


def cost_sentence_for_estimate(est: dict[str, Any]) -> str | None:
    """Deterministic one-line cost summary from a multi-channel estimate dict."""
    channels = est.get("channels")
    if not isinstance(channels, dict):
        low = est.get("cost_low")
        high = est.get("cost_high")
        if low is None:
            return None
        high = high if high is not None else low
    else:
        coverage = summarize_channels_dict(channels)
        low, high = coverage["aggregate_cost_low"], coverage["aggregate_cost_high"]
        if low is None:
            return None

    drug = est.get("drug_name") or "This drug"
    dosage = est.get("dosage")
    drug_label = f"{drug} {dosage}".strip() if dosage else drug
    days = est.get("days_supply", 30)
    plan_key = est.get("plan_key") or est.get("plan_name") or "this plan"
    cost_text = f"${low:.2f}" if low == high else f"${low:.2f}–${high:.2f}"
    channel_note = channel_wording_for_channels(channels) if channels else ""
    tier = tier_from_estimate(est)
    tier_clause = f" (Tier {tier})" if tier is not None and not _is_insulin_cap_estimate(est) else ""
    return (
        f"{drug_label.capitalize()}{tier_clause} for a {days}-day supply on {plan_key} "
        f"is estimated at {cost_text}{channel_note}."
    )


def deterministic_cost_explanation(channel_estimates: list[dict[str, Any]]) -> str | None:
    parts = [cost_sentence_for_estimate(est) for est in channel_estimates if isinstance(est, dict)]
    parts = [p for p in parts if p]
    return "\n\n".join(parts) if parts else None


def repair_false_unavailable_prose(
    explanation: str,
    channel_estimates: list[dict[str, Any]] | None,
) -> str:
    """Prepend grounded cost leads when prose wrongly claims no estimate exists."""
    if not explanation or not channel_estimates:
        return explanation

    leads: list[str] = []
    for est in channel_estimates:
        if not isinstance(est, dict):
            continue
        plan_key = est.get("plan_key") or ""
        coverage = summarize_channels_dict(est.get("channels"))
        if not coverage["priced_channels"]:
            continue
        window = (
            _plan_lead_window(explanation, plan_key)
            if plan_key
            else explanation.lower()
        )
        if not window:
            continue
        if not any(phrase in window for phrase in _UNAVAILABLE_PHRASES):
            continue
        if _window_has_dollar_for_aggregate(
            window,
            coverage["aggregate_cost_low"],
            coverage["aggregate_cost_high"],
        ):
            continue
        sentence = cost_sentence_for_estimate(est)
        if sentence and sentence not in explanation:
            leads.append(sentence)

    if not leads:
        return explanation
    return "\n\n".join(leads + [explanation.strip()])


def is_mail_retail_contrast_question(text: str) -> bool:
    return bool(text and _MAIL_RETAIL_CONTRAST_RE.search(text))


def _join_channel_price_parts(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def channel_contrast_sentence_for_estimate(est: dict[str, Any]) -> str | None:
    """Grounded mail-vs-retail breakdown when both channel types have CMS pricing."""
    channels = est.get("channels")
    if not isinstance(channels, dict):
        return None
    coverage = summarize_channels_dict(channels)
    priced = set(coverage["priced_channels"])
    mail_keys = [k for k in _MAIL_CHANNEL_KEYS if k in priced]
    retail_keys = [k for k in _RETAIL_CHANNEL_KEYS if k in priced]
    if not mail_keys or not retail_keys:
        return None

    parts: list[str] = []
    for key in mail_keys + retail_keys:
        channel = channels.get(key) or {}
        low = channel.get("cost_low")
        if low is None:
            continue
        label = PHARMACY_CHANNEL_LABELS.get(key, key)
        parts.append(f"{label} is ${float(low):.2f}")

    if not parts:
        return None

    drug = est.get("drug_name") or "This drug"
    dosage = est.get("dosage")
    drug_label = f"{drug} {dosage}".strip() if dosage else drug
    days = est.get("days_supply", 30)
    plan_key = est.get("plan_key") or est.get("plan_name") or "this plan"
    tier = tier_from_estimate(est)
    tier_clause = (
        f" (Tier {tier})"
        if tier is not None and not _is_insulin_cap_estimate(est)
        else ""
    )
    joined = _join_channel_price_parts(parts)
    return (
        f"For {drug_label}{tier_clause} on {plan_key}, a {days}-day fill is estimated at "
        f"{joined}."
    )


def repair_missing_mail_retail_contrast_in_prose(
    explanation: str,
    channel_estimates: list[dict[str, Any]] | None,
    user_message: str | None,
) -> str:
    """Prepend mail/retail channel figures when the user asked for that contrast."""
    if not explanation or not channel_estimates or not is_mail_retail_contrast_question(
        user_message or ""
    ):
        return explanation
    if "mail" in explanation.lower():
        return explanation

    leads: list[str] = []
    for est in channel_estimates:
        if not isinstance(est, dict):
            continue
        sentence = channel_contrast_sentence_for_estimate(est)
        if sentence and sentence not in explanation:
            leads.append(sentence)

    if not leads:
        return explanation
    return "\n\n".join(leads + [explanation.strip()])


def channel_wording_for_channels(channels: dict[str, Any] | None) -> str:
    """Suffix for cost sentences — never implies all four channels when data is partial."""
    coverage = summarize_channels_dict(channels)
    priced = coverage["priced_channels"]
    missing = coverage["missing_channels"]
    if not priced:
        return ""
    if len(priced) == 1:
        label = PHARMACY_CHANNEL_LABELS.get(priced[0], priced[0])
        if missing:
            return (
                f" ({label} only — CMS data has no matching estimate for other pharmacy channels)"
            )
        return f" ({label})"
    if missing:
        return (
            " depending on pharmacy channel (CMS data is missing for some channels)"
        )
    return " depending on pharmacy channel"
