from __future__ import annotations

import re
from typing import Any

from medicare_navigator.config import settings
from medicare_navigator.guardrails.source_catalog import (
    drug_name_from_artifacts,
    formulary_citation_claim,
    label_for_source_id,
    trend_citation_claim,
    url_for_source_id,
)
from medicare_navigator.models.citation import Citation
from medicare_navigator.models.response import FormularyResult

_DOLLAR_RE = re.compile(r"\$\s*\d+(?:\.\d{1,2})?")
_TIER_COPAY_RE = re.compile(
    r"\b(tier\s*\d|copay|coinsurance|formulary|cost[- ]sharing|benefit phase|deductible phase)\b",
    re.I,
)


def extract_source_ids(tool_artifacts: dict[str, dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for artifact in tool_artifacts.values():
        sid = artifact.get("source_id")
        if sid:
            ids.add(sid)
    return ids


def _passage_claim(text: str, source_label: str | None) -> str:
    if source_label:
        return source_label
    trimmed = (text or "").strip()
    if len(trimmed) > 80:
        return trimmed[:77] + "..."
    return trimmed or "CMS policy reference"


def _normalize_artifacts(tool_artifacts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for name, artifact in tool_artifacts.items():
        if hasattr(artifact, "model_dump"):
            dumped = artifact.model_dump()
            normalized[name] = {
                "status": dumped.get("status"),
                "source_id": dumped.get("source_id"),
                "as_of_date": dumped.get("as_of_date"),
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
                "data": artifact.get("data"),
            }
    return normalized


def _url_for_policy_claim(claim: str, passages: list[dict[str, Any]]) -> str | None:
    for passage in passages:
        text = passage.get("text", "")
        label = passage.get("source_label") or ""
        if claim in text or text in claim or (label and label in claim):
            return passage.get("url")
    return None


def enrich_citations(
    citations: list[Citation],
    tool_artifacts: dict[str, Any],
) -> list[Citation]:
    """Attach documentation URLs from source registry and policy passage metadata."""
    artifacts = _normalize_artifacts(tool_artifacts)
    passages = (artifacts.get("policy_retrieval") or {}).get("data") or []
    enriched: list[Citation] = []
    for citation in citations:
        if citation.url:
            enriched.append(citation)
            continue
        url = _url_for_policy_claim(citation.claim, passages)
        if not url:
            url = url_for_source_id(citation.source_id)
        enriched.append(citation.model_copy(update={"url": url}) if url else citation)
    return enriched


def build_citations_from_artifacts(
    tool_artifacts: dict[str, dict[str, Any]],
) -> list[Citation]:
    citations: list[Citation] = []
    seen: set[str] = set()
    drug_name = drug_name_from_artifacts(tool_artifacts)

    form = tool_artifacts.get("formulary_benefit_lookup")
    if form and form.get("status") in ("ok", "not_covered") and form.get("data"):
        key = f"formulary:{form['source_id']}"
        if key not in seen:
            data = form["data"]
            source_id = form["source_id"]
            citations.append(
                Citation(
                    claim=formulary_citation_claim(data, drug_name),
                    source_id=source_id,
                    as_of_date=form.get("as_of_date", ""),
                    source_label=label_for_source_id(source_id),
                    url=url_for_source_id(source_id),
                )
            )
            seen.add(key)

    trend = tool_artifacts.get("cost_trend_lookup")
    if trend and trend.get("status") == "ok" and trend.get("data"):
        key = f"trend:{trend['source_id']}"
        if key not in seen:
            points = trend["data"]
            if points:
                source_id = trend["source_id"]
                citations.append(
                    Citation(
                        claim=trend_citation_claim(points, drug_name),
                        source_id=source_id,
                        as_of_date=trend.get("as_of_date", ""),
                        source_label=label_for_source_id(source_id),
                        url=url_for_source_id(source_id),
                    )
                )
                seen.add(key)

    alts = tool_artifacts.get("alternatives_finder")
    if alts and alts.get("status") == "ok" and alts.get("data"):
        key = f"alts:{alts['source_id']}"
        if key not in seen:
            source_id = alts["source_id"]
            drug = drug_name.capitalize() if drug_name else "Drug"
            citations.append(
                Citation(
                    claim=f"{drug} therapeutic alternatives",
                    source_id=source_id,
                    as_of_date=alts.get("as_of_date", ""),
                    source_label=label_for_source_id(source_id),
                    url=url_for_source_id(source_id),
                )
            )
            seen.add(key)

    policy = tool_artifacts.get("policy_retrieval")
    if policy and policy.get("status") == "ok" and policy.get("data"):
        for idx, passage in enumerate(policy["data"]):
            key = f"policy:{policy['source_id']}:{passage.get('passage_id', idx)}"
            if key in seen:
                continue
            text = passage.get("text", "")
            label = passage.get("source_label")
            citations.append(
                Citation(
                    claim=_passage_claim(text, label),
                    source_id=policy["source_id"],
                    as_of_date=policy.get("as_of_date", ""),
                    source_label=label or "CMS Policy Corpus",
                    url=passage.get("url") or url_for_source_id(policy["source_id"]),
                )
            )
            seen.add(key)

    return citations


def _allowed_dollar_amounts(tool_artifacts: dict[str, dict[str, Any]]) -> set[str]:
    amounts: set[str] = set()
    form = tool_artifacts.get("formulary_benefit_lookup")
    if not form or not form.get("data"):
        return amounts

    data = form["data"]
    if isinstance(data, dict):
        cs = data.get("cost_share") or {}
        if cs.get("copay") is not None:
            amounts.add(f"${cs['copay']:.2f}")
            amounts.add(f"${cs['copay']:.0f}")
        if data.get("ytd_oop_spend") is not None:
            ytd = float(data["ytd_oop_spend"])
            amounts.add(f"${ytd:,.2f}")
            amounts.add(f"${ytd:,.0f}")
            amounts.add(f"${ytd:.2f}")
            amounts.add(f"${ytd:.0f}")

        supply = data.get("supply_estimate")
        if supply:
            if supply.get("estimated_patient_cost") is not None:
                est = float(supply["estimated_patient_cost"])
                amounts.add(f"${est:.2f}")
                amounts.add(f"${est:.0f}")
            for scenario in supply.get("scenarios") or []:
                est = float(scenario.get("estimated_patient_cost", 0))
                amounts.add(f"${est:.2f}")
                amounts.add(f"${est:.0f}")

    trend = tool_artifacts.get("cost_trend_lookup")
    if trend and trend.get("data"):
        for point in trend["data"]:
            for field in ("total_spend", "avg_unit_cost"):
                val = point.get(field)
                if val is not None:
                    amounts.add(f"${float(val):,.0f}")
                    amounts.add(f"${float(val):,.2f}")

    return amounts


def _has_formulary_evidence(tool_artifacts: dict[str, dict[str, Any]]) -> bool:
    form = tool_artifacts.get("formulary_benefit_lookup")
    return bool(form and form.get("status") in ("ok", "not_covered") and form.get("data"))


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

    if _TIER_COPAY_RE.search(out) and not _has_formulary_evidence(tool_artifacts):
        errors.append("Mentioned tier/copay without formulary_benefit_lookup evidence.")

    dollars_in_text = _DOLLAR_RE.findall(out)
    if dollars_in_text:
        allowed = _allowed_dollar_amounts(tool_artifacts)
        for amount in dollars_in_text:
            normalized = amount.replace(" ", "")
            if normalized not in allowed and amount not in allowed:
                errors.append(f"Dollar amount {amount} not traceable to tool results.")

    if settings.disclaimer_text and settings.disclaimer_text not in out:
        out = f"{out}\n\n{settings.disclaimer_text}"

    cites = enrich_citations(cites, tool_artifacts)
    return out, cites, errors


def formulary_from_artifact(
    tool_artifacts: dict[str, dict[str, Any]],
) -> FormularyResult | None:
    form = tool_artifacts.get("formulary_benefit_lookup")
    if not form or not form.get("data"):
        return None
    return FormularyResult.model_validate(form["data"])
