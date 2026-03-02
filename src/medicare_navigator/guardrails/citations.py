from __future__ import annotations

import re
from typing import Any

from medicare_navigator.config import settings
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


def build_citations_from_artifacts(
    tool_artifacts: dict[str, dict[str, Any]],
) -> list[Citation]:
    citations: list[Citation] = []
    seen: set[str] = set()

    form = tool_artifacts.get("formulary_benefit_lookup")
    if form and form.get("status") in ("ok", "not_covered") and form.get("data"):
        key = f"formulary:{form['source_id']}"
        if key not in seen:
            data = form["data"]
            tier = data.get("tier")
            claim = (
                f"Formulary tier {tier} on {data.get('plan_key')}"
                if tier is not None
                else f"Formulary status on {data.get('plan_key')}"
            )
            citations.append(
                Citation(
                    claim=claim,
                    source_id=form["source_id"],
                    as_of_date=form.get("as_of_date", ""),
                    source_label="CMS SPUF Demo Formulary",
                )
            )
            seen.add(key)

    trend = tool_artifacts.get("cost_trend_lookup")
    if trend and trend.get("status") == "ok" and trend.get("data"):
        key = f"trend:{trend['source_id']}"
        if key not in seen:
            points = trend["data"]
            if points:
                first, last = points[0], points[-1]
                citations.append(
                    Citation(
                        claim=f"Spending trend {first.get('year')}-{last.get('year')}",
                        source_id=trend["source_id"],
                        as_of_date=trend.get("as_of_date", ""),
                        source_label="CMS Part D Spending Demo",
                    )
                )
                seen.add(key)

    alts = tool_artifacts.get("alternatives_finder")
    if alts and alts.get("status") == "ok" and alts.get("data"):
        key = f"alts:{alts['source_id']}"
        if key not in seen:
            citations.append(
                Citation(
                    claim="Therapeutic alternatives",
                    source_id=alts["source_id"],
                    as_of_date=alts.get("as_of_date", ""),
                    source_label="FDA Orange Book Demo",
                )
            )
            seen.add(key)

    policy = tool_artifacts.get("policy_retrieval")
    if policy and policy.get("status") == "ok" and policy.get("data"):
        key = f"policy:{policy['source_id']}"
        if key not in seen:
            citations.append(
                Citation(
                    claim="CMS policy reference",
                    source_id=policy["source_id"],
                    as_of_date=policy.get("as_of_date", ""),
                    source_label="CMS Policy Corpus",
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

    return out, cites, errors


def formulary_from_artifact(
    tool_artifacts: dict[str, dict[str, Any]],
) -> FormularyResult | None:
    form = tool_artifacts.get("formulary_benefit_lookup")
    if not form or not form.get("data"):
        return None
    return FormularyResult.model_validate(form["data"])
