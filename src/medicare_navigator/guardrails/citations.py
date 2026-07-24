from __future__ import annotations

import re
from typing import Any

from medicare_navigator.config import settings
from medicare_navigator.guardrails.source_catalog import (
    drug_name_from_artifacts,
    formulary_citation_claim,
    label_for_source_id,
    url_for_source_id,
)
from medicare_navigator.models.citation import Citation
from medicare_navigator.models.response import DrugCostEstimate, MultiChannelDrugCostEstimate, MultiChannelDrugCostEstimate
from medicare_navigator.tools.pharmacy_channels import channel_cost_bounds

_ESTIMATE_TOOL_NAMES = ("estimate_drug_cost_all_channels", "estimate_drug_cost")

_DOLLAR_RE = re.compile(r"\$\s*\d+(?:\.\d{1,2})?")
_TIER_COPAY_RE = re.compile(
    r"\b(tier\s*\d|copay|coinsurance|formulary|cost[- ]sharing|benefit phase|deductible phase)\b",
    re.I,
)

# Phase language the LLM might use in prose, mapped to the tool's actual phase values, so a
# stated phase can be checked against what the estimate tool actually returned (decision: an
# LLM explanation must not assert a benefit phase the tool data contradicts, even if the
# dollar figure it quotes happens to be correct in both phases).
_PRE_DEDUCTIBLE_PHASE_RE = re.compile(r"\bpre[- ]deductible\b", re.I)
_POST_DEDUCTIBLE_PHASE_RE = re.compile(
    r"\binitial[- ]coverage\b|\bpost[- ]deductible\b|\bafter (?:your |the )?deductible\b"
    r"|\b(?:met|reached|satisfied) (?:your |the )?deductible\b",
    re.I,
)

# Statuses whose caveats/messages are hard-stops or safety-critical disclaimers that must
# reach the user verbatim — an LLM paraphrase must not be allowed to drop them (decision 4).
_ENFORCED_STATUSES = {"suppressed", "insulin_out_of_scope", "quantity_limit_blocked"}

# Tool outcomes that queried a registered data source but did not produce an estimate.
_CITABLE_LOOKUP_STATUSES = frozenset(
    {
        "not_found",
        "not_covered",
        "suppressed",
        "insulin_out_of_scope",
        "quantity_limit_blocked",
    }
)


def _primary_estimate_artifact(
    tool_artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    for name in _ESTIMATE_TOOL_NAMES:
        artifact = tool_artifacts.get(name)
        if artifact:
            return artifact
    return None


def _estimate_data(tool_artifacts: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    artifact = _primary_estimate_artifact(tool_artifacts)
    if not artifact:
        return None
    data = artifact.get("data")
    return data if isinstance(data, dict) else None


def extract_source_ids(tool_artifacts: dict[str, dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for artifact in tool_artifacts.values():
        sid = artifact.get("source_id")
        if sid:
            ids.add(sid)
    return ids


def _normalize_artifacts(tool_artifacts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for name, artifact in tool_artifacts.items():
        if hasattr(artifact, "model_dump"):
            dumped = artifact.model_dump()
            normalized[name] = {
                "status": dumped.get("status"),
                "source_id": dumped.get("source_id"),
                "as_of_date": dumped.get("as_of_date"),
                "message": dumped.get("message"),
                "data": dumped.get("data"),
            }
        elif isinstance(artifact, dict):
            status = artifact.get("status")
            if hasattr(status, "value"):
                status = status.value
            normalized[name] = {
                "status": status,
                "source_id": artifact.get("source_id"),
                "as_of_date": artifact.get("as_of_date"),
                "message": artifact.get("message"),
                "data": artifact.get("data"),
            }
    return normalized


def enrich_citations(
    citations: list[Citation],
    tool_artifacts: dict[str, Any],
) -> list[Citation]:
    """Attach documentation URLs from the source registry."""
    enriched: list[Citation] = []
    for citation in citations:
        if citation.url:
            enriched.append(citation)
            continue
        url = url_for_source_id(citation.source_id)
        enriched.append(citation.model_copy(update={"url": url}) if url else citation)
    return enriched


def _citation_from_artifact(artifact: dict[str, Any]) -> Citation:
    source_id = artifact["source_id"]
    claim = artifact.get("message") or "Record lookup completed."
    return Citation(
        claim=claim,
        source_id=source_id,
        as_of_date=artifact.get("as_of_date", ""),
        source_label=label_for_source_id(source_id),
        url=url_for_source_id(source_id),
    )


def build_citations_from_artifacts(
    tool_artifacts: dict[str, dict[str, Any]],
) -> list[Citation]:
    citations: list[Citation] = []
    drug_name = drug_name_from_artifacts(tool_artifacts)

    estimate = _primary_estimate_artifact(tool_artifacts)
    if estimate and estimate.get("status") in ("ok", "not_covered") and estimate.get("data"):
        data = estimate["data"]
        source_id = estimate["source_id"]
        citations.append(
            Citation(
                claim=formulary_citation_claim(data, drug_name),
                source_id=source_id,
                as_of_date=estimate.get("as_of_date", ""),
                source_label=label_for_source_id(source_id),
                url=url_for_source_id(source_id),
            )
        )
    elif (
        estimate
        and estimate.get("source_id")
        and estimate.get("status") in _CITABLE_LOOKUP_STATUSES
    ):
        citations.append(_citation_from_artifact(estimate))

    if citations:
        return citations

    lookup = tool_artifacts.get("lookup_plan")
    if lookup and lookup.get("source_id"):
        if lookup.get("status") == "not_found":
            citations.append(_citation_from_artifact(lookup))
        elif lookup.get("status") == "ok":
            data = lookup.get("data")
            plan = data.get("plan") if isinstance(data, dict) else None
            if plan:
                source_id = lookup["source_id"]
                citations.append(
                    Citation(
                        claim=(
                            f"Plan {plan['plan_key']} ({plan['plan_name']}) "
                            "found in CMS database"
                        ),
                        source_id=source_id,
                        as_of_date=lookup.get("as_of_date", ""),
                        source_label=label_for_source_id(source_id),
                        url=url_for_source_id(source_id),
                    )
                )

    return citations


def _allowed_dollar_amounts(tool_artifacts: dict[str, dict[str, Any]]) -> set[float]:
    amounts: set[float] = set()
    data = _estimate_data(tool_artifacts)
    if data:
        channels = data.get("channels")
        if channels:
            cost_low, cost_high = channel_cost_bounds(channels)
            if cost_low is not None:
                amounts.add(round(float(cost_low), 2))
            if cost_high is not None:
                amounts.add(round(float(cost_high), 2))
            for channel in channels.values():
                if isinstance(channel, dict):
                    for field in ("cost_low", "cost_high"):
                        value = channel.get(field)
                        if value is not None:
                            amounts.add(round(float(value), 2))
        else:
            for field in ("cost_low", "cost_high"):
                value = data.get(field)
                if value is not None:
                    amounts.add(round(float(value), 2))

    lookup = tool_artifacts.get("lookup_plan")
    if lookup and lookup.get("status") == "ok":
        plan = (lookup.get("data") or {}).get("plan") or {}
        deductible = plan.get("deductible")
        if deductible is not None:
            amounts.add(round(float(deductible), 2))

    return amounts


def _has_formulary_evidence(tool_artifacts: dict[str, dict[str, Any]]) -> bool:
    """True whenever an estimate tool ran and resolved the plan/drug — a legitimate
    not_covered result has data=None by design, so data-truthiness alone would wrongly
    flag a correct "not on formulary" answer as an unbacked claim."""
    estimate = _primary_estimate_artifact(tool_artifacts)
    return bool(estimate and estimate.get("status") in ("ok", "not_covered"))


def _enforced_texts(tool_artifacts: dict[str, dict[str, Any]]) -> list[str]:
    """Verbatim caveats/messages that must survive into the final explanation untouched."""
    texts: list[str] = []
    estimate = _primary_estimate_artifact(tool_artifacts)
    if not estimate:
        return texts
    status = estimate.get("status")
    if status in _ENFORCED_STATUSES and estimate.get("message"):
        texts.append(estimate["message"])
    data = estimate.get("data")
    if isinstance(data, dict):
        for caveat in data.get("caveats") or []:
            texts.append(caveat)
    return texts


def _stated_phase_mismatch(out: str, tool_artifacts: dict[str, dict[str, Any]]) -> str | None:
    """Return an error string if the explanation names a benefit phase that contradicts the
    actual phase the estimate tool returned for this turn (e.g. hallucinating "pre-deductible"
    after the user reported meeting their deductible and a re-call returned initial_coverage)."""
    mentions_pre = bool(_PRE_DEDUCTIBLE_PHASE_RE.search(out))
    mentions_post = bool(_POST_DEDUCTIBLE_PHASE_RE.search(out))
    if not mentions_pre and not mentions_post:
        return None

    data = _estimate_data(tool_artifacts)
    if not data:
        return "Stated a benefit phase without a matching estimate tool call this turn."

    actual_phase = data.get("effective_phase") or data.get("benefit_phase")
    if not actual_phase:
        return None

    if mentions_pre and actual_phase != "pre_deductible":
        return (
            f"Explanation says 'pre-deductible' but the tool result's phase is "
            f"'{actual_phase}' — restate using the actual phase."
        )
    if mentions_post and actual_phase != "initial_coverage":
        return (
            f"Explanation implies deductible already met but the tool result's phase is "
            f"'{actual_phase}' — restate using the actual phase."
        )
    return None


def apply_guardrails(
    explanation: str,
    tool_artifacts: dict[str, dict[str, Any]],
    citations: list[Citation] | None = None,
) -> tuple[str, list[Citation], list[str]]:
    """Validate and fix explanation. Returns (explanation, citations, errors)."""
    errors: list[str] = []
    out = explanation.strip()
    cites = list(citations or build_citations_from_artifacts(tool_artifacts))

    valid_source_ids = extract_source_ids(tool_artifacts)
    cites = [c for c in cites if c.source_id in valid_source_ids]

    estimate = _primary_estimate_artifact(tool_artifacts) or {}
    is_hard_stop = estimate.get("status") in _ENFORCED_STATUSES

    if _TIER_COPAY_RE.search(out) and not _has_formulary_evidence(tool_artifacts):
        errors.append("Mentioned tier/copay without estimate tool evidence.")

    phase_error = _stated_phase_mismatch(out, tool_artifacts)
    if phase_error:
        errors.append(phase_error)

    # Hard-stop messages (e.g. insulin's statutory $35/month cap) are pre-approved verbatim
    # text, not LLM-invented figures — don't run the dollar-traceability check against them.
    dollars_in_text = [] if is_hard_stop else _DOLLAR_RE.findall(out)
    if dollars_in_text:
        allowed = _allowed_dollar_amounts(tool_artifacts)
        for amount in dollars_in_text:
            digits = amount.replace("$", "").replace(" ", "")
            try:
                value = round(float(digits), 2)
            except ValueError:
                value = None
            if value is None or value not in allowed:
                errors.append(f"Dollar amount {amount} not traceable to tool results.")

    # Safety-critical caveats/hard-stop messages must reach the user verbatim, not just be
    # requested via the system prompt — force-append any the LLM dropped or paraphrased away.
    for text in _enforced_texts(tool_artifacts):
        if text and text not in out:
            out = f"{out}\n\n{text}"

    if settings.disclaimer_text and settings.disclaimer_text not in out:
        out = f"{out}\n\n{settings.disclaimer_text}"

    cites = enrich_citations(cites, tool_artifacts)
    return out, cites, errors


def channel_estimate_from_artifact(
    tool_artifacts: dict[str, dict[str, Any]],
) -> MultiChannelDrugCostEstimate | None:
    """Full per-channel estimate from the agent tool (for UI cards)."""
    estimate = _primary_estimate_artifact(tool_artifacts)
    if not estimate or not estimate.get("data"):
        return None
    data = estimate["data"]
    if not isinstance(data, dict) or "channels" not in data:
        return None
    return MultiChannelDrugCostEstimate.model_validate(data)


def estimate_from_artifact(
    tool_artifacts: dict[str, dict[str, Any]],
) -> DrugCostEstimate | None:
    estimate = _primary_estimate_artifact(tool_artifacts)
    if not estimate or not estimate.get("data"):
        return None
    data = estimate["data"]
    if not isinstance(data, dict):
        return None
    if data.get("channels"):
        cost_low, cost_high = channel_cost_bounds(data["channels"])
        tiers = data.get("tiers_matched") or []
        tier = data.get("tier")
        if tier is not None and tier not in tiers:
            tiers = [tier, *tiers]
        return DrugCostEstimate(
            plan_key=data["plan_key"],
            plan_name=data["plan_name"],
            drug_name=data.get("drug_name") or "",
            rxcui=data.get("rxcui"),
            tiers_matched=tiers,
            matched_ndc_count=data.get("matched_ndc_count", 0),
            same_tier=data.get("same_tier", True),
            days_supply=data["days_supply"],
            benefit_phase=data.get("benefit_phase"),
            cost_low=cost_low,
            cost_high=cost_high,
            caveats=data.get("caveats") or [],
            quantity_limit_blocked=data.get("quantity_limit_blocked", False),
            max_allowed_days_supply=data.get("max_allowed_days_supply"),
            covered=data.get("covered") if data.get("covered") is not None else True,
        )
    return DrugCostEstimate.model_validate(data)
