from __future__ import annotations

import re
from typing import Any

from medicare_navigator.config import settings
from medicare_navigator.guardrails.channel_parity import (
    channel_coverage_note,
    prose_channel_overclaim_warnings,
    prose_false_unavailable_warnings,
    prose_tied_lowest_warnings,
    repair_false_unavailable_prose,
    repair_missing_mail_retail_contrast_in_prose,
    repair_missing_tier_in_prose,
    repair_misleading_channel_variance_in_prose,
    summarize_channel_coverage,
    text_claims_no_estimate,
    deterministic_cost_explanation,
    cost_sentence_for_estimate,
)
from medicare_navigator.guardrails.source_catalog import (
    drug_name_from_artifacts,
    formulary_citation_claim,
    label_for_source_id,
    url_for_source_id,
)
from medicare_navigator.models.citation import Citation
from medicare_navigator.models.response import DrugCostEstimate, MultiChannelDrugCostEstimate
from medicare_navigator.tools.disclaimers import (
    BUG2_CAVEAT,
    INSULIN_STATUTORY_CAP_CAVEAT,
    NO_COST_SHARE_DATA_MESSAGE,
)
from medicare_navigator.tools.drug_lookup import COMMON_DRUGS_REQUIRING_DOSAGE
from medicare_navigator.tools.normalize_drug import canonicalize_drug_name
from medicare_navigator.tools.pharmacy_channels import channel_cost_bounds

_ESTIMATE_TOOL_NAMES = ("estimate_drug_cost_all_channels", "estimate_drug_cost")

_DOLLAR_RE = re.compile(r"\$\s*\d+(?:\.\d{1,2})?")
_LLM_DISCLAIMER_TAIL_RE = re.compile(
    r"\n+(?:---\s*)?\n*(?:\*\*)?(?:General disclaimer|Disclaimer|Descargo de responsabilidad)"
    r"[\s\S]*$",
    re.I,
)
# Routine estimate caveats shown in the structured estimate card — not duplicated in chat prose.
_CARD_ONLY_CAVEATS = frozenset({BUG2_CAVEAT, INSULIN_STATUTORY_CAP_CAVEAT})
_BUG5_CAVEAT_RE = re.compile(
    r"This estimate is based on \d+ formulary NDCs for this drug",
    re.I,
)


def _is_card_only_caveat(caveat: str) -> bool:
    return caveat in _CARD_ONLY_CAVEATS or bool(_BUG5_CAVEAT_RE.search(caveat))
_BUG2_PARAPHRASE_RE = re.compile(
    r"(?:\n\n|\n)?This estimate assumes the deductible[\s\S]*?Confirm with your plan\.\s*",
    re.I,
)
_TIER_COPAY_RE = re.compile(
    r"\b(tier\s*\d|copay|coinsurance|formulary|cost[- ]sharing|benefit phase|deductible phase)\b",
    re.I,
)
_ALTERNATIVES_TOPIC_RE = re.compile(r"\balternativ|substitute", re.I)
_CLINICIAN_DEFERRAL_RE = re.compile(
    r"\b(?:doctor|pharmacist|prescriber|clinician|physician)\b",
    re.I,
)
_EXAMPLE_DRUG_AFTER_ALTERNATIVES_RE = re.compile(
    r"(?:alternativ|substitute|instead of|generic version of|cheaper)[\s\S]{0,400}"
    r"\b(?:metformin|glipizide|glimepiride|sitagliptin|semaglutide|empagliflozin|dapagliflozin)\b",
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
        if not isinstance(artifact, dict):
            continue
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


def _estimate_dedup_key(data: dict[str, Any]) -> tuple[Any, ...]:
    return (
        data.get("plan_key"),
        (data.get("drug_name") or "").lower(),
        data.get("dosage"),
        data.get("days_supply"),
    )


def build_citations_from_artifacts(
    tool_artifacts: dict[str, dict[str, Any]],
) -> list[Citation]:
    citations: list[Citation] = []
    drug_name = drug_name_from_artifacts(tool_artifacts)
    seen_keys: set[tuple[Any, ...]] = set()

    def _add_estimate_citation(artifact: dict[str, Any] | None) -> None:
        if not artifact:
            return
        data = artifact.get("data")
        status = artifact.get("status")
        if isinstance(data, dict) and status in ("ok", "not_covered"):
            key = _estimate_dedup_key(data)
            if key in seen_keys:
                return
            seen_keys.add(key)
            source_id = artifact["source_id"]
            citations.append(
                Citation(
                    claim=formulary_citation_claim(data, data.get("drug_name") or drug_name),
                    source_id=source_id,
                    as_of_date=artifact.get("as_of_date", ""),
                    source_label=label_for_source_id(source_id),
                    url=url_for_source_id(source_id),
                )
            )
        elif artifact.get("source_id") and status in _CITABLE_LOOKUP_STATUSES:
            key = (
                status,
                artifact.get("message"),
                data.get("plan_key") if isinstance(data, dict) else None,
            )
            if key in seen_keys:
                return
            seen_keys.add(key)
            citations.append(_citation_from_artifact(artifact))

    # Distinct calls made this turn (multi-drug / plan-comparison) — includes the primary call.
    calls = tool_artifacts.get("estimate_drug_cost_all_channels__calls")
    if calls:
        for artifact in calls:
            _add_estimate_citation(artifact)
    else:
        _add_estimate_citation(_primary_estimate_artifact(tool_artifacts))

    if citations:
        return citations

    benefit = tool_artifacts.get("get_part_d_benefit_params")
    if benefit and benefit.get("status") == "ok":
        data = benefit.get("data") or {}
        cap = data.get("annual_oop_cap")
        year = data.get("contract_year")
        if cap is not None:
            source_id = benefit["source_id"]
            citations.append(
                Citation(
                    claim=(
                        f"CMS Part D annual out-of-pocket maximum for {year}: "
                        f"${float(cap):,.2f}"
                    ),
                    source_id=source_id,
                    as_of_date=benefit.get("as_of_date", ""),
                    source_label=label_for_source_id(source_id),
                    url=url_for_source_id(source_id),
                )
            )
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


def _add_estimate_dollar_amounts(
    amounts: set[float], data: dict[str, Any] | None
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """Add data's dollar fields to `amounts`; return per-fill and remaining-year bounds."""
    if not data:
        return None, None
    channels = data.get("channels")
    fill_bounds = None
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
        if cost_low is not None:
            fill_bounds = (float(cost_low), float(cost_high) if cost_high is not None else float(cost_low))
    else:
        for field in ("cost_low", "cost_high"):
            value = data.get(field)
            if value is not None:
                amounts.add(round(float(value), 2))
        low, high = data.get("cost_low"), data.get("cost_high")
        if low is not None:
            fill_bounds = (float(low), float(high) if high is not None else float(low))

    remaining_bounds = None
    for field in (
        "annual_budget_cost_low",
        "annual_budget_cost_high",
        "remaining_year_budget_cost_low",
        "remaining_year_budget_cost_high",
        "annual_oop_cap",
        "remaining_oop_headroom",
    ):
        value = data.get(field)
        if value is not None:
            amounts.add(round(float(value), 2))
    r_low, r_high = data.get("remaining_year_budget_cost_low"), data.get("remaining_year_budget_cost_high")
    if r_low is not None:
        remaining_bounds = (float(r_low), float(r_high) if r_high is not None else float(r_low))

    return fill_bounds, remaining_bounds


def _estimate_phases(tool_artifacts: dict[str, dict[str, Any]]) -> set[str]:
    phases: set[str] = set()
    calls = tool_artifacts.get("estimate_drug_cost_all_channels__calls")
    if calls:
        artifacts = calls
    else:
        primary = _primary_estimate_artifact(tool_artifacts)
        artifacts = [primary] if primary else []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        data = artifact.get("data")
        if not isinstance(data, dict):
            continue
        phase = data.get("effective_phase") or data.get("benefit_phase")
        if phase:
            phases.add(str(phase))
    return phases


def _allowed_dollar_amounts(tool_artifacts: dict[str, dict[str, Any]]) -> set[float]:
    amounts: set[float] = set()

    # Every distinct estimate call this turn contributes its own dollar figures — a multi-drug
    # or plan-comparison turn calls the estimate tool more than once, and each call's cost_low/
    # cost_high must be traceable even though only the last call is the "primary" one.
    calls = tool_artifacts.get("estimate_drug_cost_all_channels__calls")
    item_bounds: list[tuple[float, float]] = []
    remaining_year_bounds: list[tuple[float, float]] = []
    if calls:
        for artifact in calls:
            if not isinstance(artifact, dict):
                continue
            data = artifact.get("data")
            fill_bounds, period_bounds = _add_estimate_dollar_amounts(
                amounts, data if isinstance(data, dict) else None
            )
            if fill_bounds:
                item_bounds.append(fill_bounds)
            if period_bounds:
                remaining_year_bounds.append(period_bounds)
    else:
        fill_bounds, period_bounds = _add_estimate_dollar_amounts(amounts, _estimate_data(tool_artifacts))
        if fill_bounds:
            item_bounds.append(fill_bounds)
        if period_bounds:
            remaining_year_bounds.append(period_bounds)

    # Deterministic insulin prose cites the federal statutory cap; it is not a tool-computed
    # figure but must not trigger repair_untraceable_dollar_prose on policy-ceiling answers.
    if "insulin_cap" in _estimate_phases(tool_artifacts):
        amounts.add(35.0)

    # A combined total (sum of each item's low/high) is only "traceable" when it's exactly the
    # sum this module itself computes — the LLM is never allowed to invent its own arithmetic.
    if len(item_bounds) > 1:
        amounts.add(round(sum(b[0] for b in item_bounds), 2))
        amounts.add(round(sum(b[1] for b in item_bounds), 2))
    if len(remaining_year_bounds) > 1:
        amounts.add(round(sum(b[0] for b in remaining_year_bounds), 2))
        amounts.add(round(sum(b[1] for b in remaining_year_bounds), 2))

    lookup = tool_artifacts.get("lookup_plan")
    if lookup and lookup.get("status") == "ok":
        plan = (lookup.get("data") or {}).get("plan") or {}
        deductible = plan.get("deductible")
        if deductible is not None:
            amounts.add(round(float(deductible), 2))

    benefit = tool_artifacts.get("get_part_d_benefit_params")
    if benefit and benefit.get("status") == "ok":
        cap = (benefit.get("data") or {}).get("annual_oop_cap")
        if cap is not None:
            amounts.add(round(float(cap), 2))

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
    seen: set[str] = set()

    def _collect(artifact: dict[str, Any] | None) -> None:
        if not artifact:
            return
        status = artifact.get("status")
        message = artifact.get("message")
        if status in _ENFORCED_STATUSES and message and message not in seen:
            texts.append(message)
            seen.add(message)
        data = artifact.get("data")
        if isinstance(data, dict):
            for caveat in data.get("caveats") or []:
                if _is_card_only_caveat(caveat):
                    continue
                if caveat not in seen:
                    texts.append(caveat)
                    seen.add(caveat)

    calls = tool_artifacts.get("estimate_drug_cost_all_channels__calls")
    if calls:
        for artifact in calls:
            _collect(artifact)
    else:
        _collect(_primary_estimate_artifact(tool_artifacts))
    return texts


def _stated_phase_mismatch(out: str, tool_artifacts: dict[str, dict[str, Any]]) -> str | None:
    """Return an error string if the explanation names a benefit phase that contradicts every
    actual phase the estimate tool(s) returned for this turn (e.g. hallucinating "pre-deductible"
    after the user reported meeting their deductible and a re-call returned initial_coverage).
    A multi-drug or plan-comparison turn can legitimately mix phases across items, so a mention
    is only flagged if it matches none of this turn's calls."""
    mentions_pre = bool(_PRE_DEDUCTIBLE_PHASE_RE.search(out))
    mentions_post = bool(_POST_DEDUCTIBLE_PHASE_RE.search(out))
    if not mentions_pre and not mentions_post:
        return None

    phases: set[str] = set()
    calls = tool_artifacts.get("estimate_drug_cost_all_channels__calls")
    if calls:
        for artifact in calls:
            if not isinstance(artifact, dict):
                continue
            data = artifact.get("data")
            if isinstance(data, dict):
                phase = data.get("effective_phase") or data.get("benefit_phase")
                if phase:
                    phases.add(phase)
    else:
        data = _estimate_data(tool_artifacts)
        if data:
            phase = data.get("effective_phase") or data.get("benefit_phase")
            if phase:
                phases.add(phase)

    if not phases:
        return "Stated a benefit phase without a matching estimate tool call this turn."

    if mentions_pre and "pre_deductible" not in phases:
        return (
            "Explanation says 'pre-deductible' but no tool result this turn returned that "
            f"phase (got: {', '.join(sorted(phases))}) — restate using the actual phase."
        )
    if mentions_post and "initial_coverage" not in phases:
        return (
            "Explanation implies deductible already met but no tool result this turn returned "
            f"'initial_coverage' phase (got: {', '.join(sorted(phases))}) — restate using the "
            "actual phase."
        )
    return None


def _prose_covers_channel_gaps(out: str) -> bool:
    lower = out.lower()
    if "depending on pharmacy channel" in lower:
        return True
    if "preferred retail" in lower and "standard retail" in lower:
        return True
    if "no matching" in lower and ("mail" in lower or "channel" in lower):
        return True
    return False


def _strip_card_only_caveat_paraphrases(out: str) -> str:
    """Remove LLM paraphrases of caveats that already appear on the estimate card."""
    return _BUG2_PARAPHRASE_RE.sub("\n", out).strip()


def _alternatives_without_clinician_deferral(out: str) -> str | None:
    if not _ALTERNATIVES_TOPIC_RE.search(out):
        return None
    if _EXAMPLE_DRUG_AFTER_ALTERNATIVES_RE.search(out):
        return (
            "Named substitute drugs without the user naming them — do not list example "
            "drug names when discussing alternatives; defer to a doctor or pharmacist and "
            "offer to estimate only drugs the user names."
        )
    if _CLINICIAN_DEFERRAL_RE.search(out):
        return None
    return (
        "Discussed alternatives without deferring clinical judgment to a doctor or "
        "pharmacist first."
    )


def _drop_contradictory_unavailable_paragraphs(out: str) -> str:
    """Remove paragraphs that deny an estimate after a repair lead stated one."""
    paragraphs = [p.strip() for p in out.split("\n\n") if p.strip()]
    kept: list[str] = []
    for paragraph in paragraphs:
        if text_claims_no_estimate(paragraph):
            continue
        kept.append(paragraph)
    return "\n\n".join(kept) if kept else out


_FALSE_NOT_COVERED_RE = re.compile(
    r"\bnot\s+covered\b|\bnot\s+on\s+(?:the\s+)?(?:plan'?s?\s+)?formulary\b",
    re.I,
)


def _estimate_calls(tool_artifacts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    calls = tool_artifacts.get("estimate_drug_cost_all_channels__calls")
    if calls:
        return [artifact for artifact in calls if isinstance(artifact, dict)]
    primary = _primary_estimate_artifact(tool_artifacts)
    return [primary] if primary else []


def _covered_estimate_calls(
    tool_artifacts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        artifact
        for artifact in _estimate_calls(tool_artifacts)
        if artifact.get("status") == "ok" and (artifact.get("data") or {}).get("covered") is True
    ]


def repair_false_not_covered_for_missing_dosage(
    explanation: str, tool_artifacts: dict[str, dict[str, Any]]
) -> str:
    """Ingredient-level not_covered rows without a strength are not definitive coverage denials."""
    if not _FALSE_NOT_COVERED_RE.search(explanation):
        return explanation

    calls = _estimate_calls(tool_artifacts)
    if not calls:
        return explanation

    needs_dosage = any(artifact.get("status") == "needs_dosage" for artifact in calls)
    spurious_drugs: list[str] = []
    for artifact in calls:
        if artifact.get("status") != "not_covered":
            continue
        data = artifact.get("data") or {}
        if data.get("dosage"):
            continue
        drug = canonicalize_drug_name(data.get("drug_name") or "")
        plan_key = data.get("plan_key")
        if not drug:
            continue
        has_covered_variant = any(
            other.get("status") == "ok"
            and (other.get("data") or {}).get("covered")
            and canonicalize_drug_name((other.get("data") or {}).get("drug_name") or "") == drug
            and (other.get("data") or {}).get("plan_key") == plan_key
            for other in calls
        )
        if has_covered_variant or drug in COMMON_DRUGS_REQUIRING_DOSAGE or needs_dosage:
            if drug not in spurious_drugs:
                spurious_drugs.append(drug)

    if not spurious_drugs:
        return explanation

    if len(spurious_drugs) == 1:
        drug = spurious_drugs[0]
        return (
            f"I need the strength (dosage) for **{drug}** before I can estimate cost or "
            "confirm formulary coverage — missing strength is not the same as not covered."
        )
    drug_list = ", ".join(f"**{d}**" for d in spurious_drugs)
    return (
        f"I need the strength for each drug ({drug_list}) before I can compare costs — "
        "missing strength is not the same as not covered."
    )


def _untraceable_dollar_amounts(
    explanation: str,
    tool_artifacts: dict[str, dict[str, Any]],
) -> list[str]:
    allowed = _allowed_dollar_amounts(tool_artifacts)
    untraceable: list[str] = []
    for amount in _DOLLAR_RE.findall(explanation):
        digits = amount.replace("$", "").replace(" ", "")
        try:
            value = round(float(digits), 2)
        except ValueError:
            value = None
        if value is None or value not in allowed:
            untraceable.append(amount)
    return untraceable


def repair_untraceable_dollar_prose(
    explanation: str,
    tool_artifacts: dict[str, dict[str, Any]],
    channel_estimates: list[dict[str, Any]] | None = None,
) -> str:
    """Replace prose that cites invented dollar amounts with deterministic tool-backed text."""
    if not _untraceable_dollar_amounts(explanation, tool_artifacts):
        return explanation
    channel_estimates = channel_estimates or [
        est.model_dump() for est in channel_estimates_from_artifact(tool_artifacts)
    ]
    deterministic = deterministic_cost_explanation(channel_estimates)
    if deterministic:
        return deterministic
    sentences: list[str] = []
    for artifact in _covered_estimate_calls(tool_artifacts):
        data = artifact.get("data") or {}
        sentence = cost_sentence_for_estimate(data)
        if sentence and sentence not in sentences:
            sentences.append(sentence)
    if sentences:
        return "\n\n".join(sentences)
    return explanation


def repair_false_not_covered_when_covered(
    explanation: str,
    tool_artifacts: dict[str, dict[str, Any]],
    channel_estimates: list[dict[str, Any]] | None = None,
) -> str:
    """Replace prose that denies coverage when estimate tool(s) returned covered=true."""
    if not _FALSE_NOT_COVERED_RE.search(explanation):
        return explanation
    covered_calls = _covered_estimate_calls(tool_artifacts)
    if not covered_calls:
        return explanation
    if channel_estimates:
        deterministic = deterministic_cost_explanation(channel_estimates)
        if deterministic:
            return deterministic
    sentences: list[str] = []
    for artifact in covered_calls:
        data = artifact.get("data") or {}
        sentence = cost_sentence_for_estimate(data)
        if sentence and sentence not in sentences:
            sentences.append(sentence)
    if sentences:
        return "\n\n".join(sentences)
    return explanation


def apply_guardrails(
    explanation: str,
    tool_artifacts: dict[str, dict[str, Any]],
    citations: list[Citation] | None = None,
    *,
    channel_estimates: list[dict[str, Any]] | None = None,
    user_message: str | None = None,
) -> tuple[str, list[Citation], list[str]]:
    """Validate and fix explanation. Returns (explanation, citations, errors)."""
    errors: list[str] = []
    explanation = repair_false_not_covered_for_missing_dosage(explanation, tool_artifacts)
    channel_estimates = channel_estimates or [
        est.model_dump() for est in channel_estimates_from_artifact(tool_artifacts)
    ]
    explanation = repair_untraceable_dollar_prose(
        explanation, tool_artifacts, channel_estimates
    )
    repaired_not_covered = repair_false_not_covered_when_covered(
        explanation, tool_artifacts, channel_estimates
    )
    had_repair = repaired_not_covered != explanation
    explanation = repaired_not_covered
    channel_coverage = summarize_channel_coverage(channel_estimates)
    priced_channels_exist = any(
        (summary.get("priced_channels") or []) for summary in channel_coverage
    )
    if priced_channels_exist and text_claims_no_estimate(explanation):
        deterministic = deterministic_cost_explanation(channel_estimates)
        if deterministic:
            repaired = deterministic
            had_repair = True
        else:
            repaired = repair_false_unavailable_prose(explanation.strip(), channel_estimates)
            had_repair = had_repair or repaired != explanation.strip()
    else:
        repaired = repair_false_unavailable_prose(explanation.strip(), channel_estimates)
        had_repair = had_repair or repaired != explanation.strip()
    out = _strip_card_only_caveat_paraphrases(repaired)
    if had_repair:
        out = _drop_contradictory_unavailable_paragraphs(out)
    if _has_formulary_evidence(tool_artifacts):
        out = repair_missing_tier_in_prose(out, channel_estimates)
    out = repair_missing_mail_retail_contrast_in_prose(
        out, channel_estimates, user_message
    )
    out = repair_misleading_channel_variance_in_prose(out, channel_estimates)
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

    alternatives_error = _alternatives_without_clinician_deferral(out)
    if alternatives_error:
        errors.append(alternatives_error)

    channel_coverage = summarize_channel_coverage(channel_estimates)
    for warning in prose_channel_overclaim_warnings(out, channel_coverage):
        errors.append(warning)
    for warning in prose_false_unavailable_warnings(out, channel_coverage):
        errors.append(warning)
    for warning in prose_tied_lowest_warnings(out, channel_coverage):
        errors.append(warning)
    coverage_note = channel_coverage_note(channel_coverage)
    if coverage_note and coverage_note not in out and not _prose_covers_channel_gaps(out):
        out = f"{out}\n\n{coverage_note}"

    # Hard-stop messages (suppressed plan, insulin data-gap, quantity limit) are pre-approved
    # verbatim text, not LLM-invented figures — don't run dollar-traceability against them.
    if not is_hard_stop:
        for amount in _untraceable_dollar_amounts(out, tool_artifacts):
            errors.append(f"Dollar amount {amount} not traceable to tool results.")

    # Safety-critical caveats/hard-stop messages must reach the user verbatim, not just be
    # requested via the system prompt — force-append any the LLM dropped or paraphrased away.
    priced_channels_exist = any(
        (summary.get("priced_channels") or []) for summary in channel_coverage
    )
    for text in _enforced_texts(tool_artifacts):
        if (
            priced_channels_exist
            and text
            and NO_COST_SHARE_DATA_MESSAGE in text
        ):
            continue
        if text and text not in out:
            out = f"{out}\n\n{text}"

    out = _LLM_DISCLAIMER_TAIL_RE.sub("", out).rstrip()
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


def channel_estimates_from_artifact(
    tool_artifacts: dict[str, dict[str, Any]],
) -> list[MultiChannelDrugCostEstimate]:
    """All distinct multi-channel estimates from estimate_drug_cost_all_channels calls made
    this turn (multi-drug basket / plan comparison), de-duplicated by
    (plan_key, drug_name, dosage, days_supply). Falls back to the single primary call when the
    agent made only one estimate call this turn."""
    calls = tool_artifacts.get("estimate_drug_cost_all_channels__calls")
    if not calls:
        single = channel_estimate_from_artifact(tool_artifacts)
        return [single] if single is not None else []

    seen: set[tuple[Any, ...]] = set()
    estimates: list[MultiChannelDrugCostEstimate] = []
    for artifact in calls:
        data = artifact.get("data") if isinstance(artifact, dict) else None
        if not isinstance(data, dict) or "channels" not in data:
            continue
        key = _estimate_dedup_key(data)
        if key in seen:
            continue
        seen.add(key)
        estimates.append(MultiChannelDrugCostEstimate.model_validate(data))
    return estimates


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
