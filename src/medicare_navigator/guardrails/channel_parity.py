"""Channel coverage helpers for guardrails and chat-QA grading."""

from __future__ import annotations

from typing import Any

from medicare_navigator.tools.pharmacy_channels import PHARMACY_CHANNEL_LABELS, PHARMACY_CHANNELS

_ALL_CHANNELS_PHRASES = (
    "all cms pharmacy channel",
    "all four cms",
    "all pharmacy channel",
    "every pharmacy channel",
    "all channels",
)


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
