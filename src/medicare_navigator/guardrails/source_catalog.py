"""Human-readable labels and documentation URLs for data sources."""

from __future__ import annotations

from typing import Any

SOURCE_CATALOG: dict[str, dict[str, str]] = {
    "cms_spuf_2026_q1": {
        "label": "CMS Part D Formulary & Pricing (SPUF)",
        "url": (
            "https://data.cms.gov/provider-summary-by-type-of-service/"
            "medicare-part-d-prescribers/quarterly-prescription-drug-plan-formulary-pharmacy-network-and-pricing-information"
        ),
        "scope": "Plan-specific tier, copay, and benefit phase",
    },
    "cms_part_d_spending": {
        "label": "CMS Medicare Part D Drug Spending",
        "url": (
            "https://data.cms.gov/summary-statistics-on-use-and-payments/"
            "medicare-medicaid-spending-by-drug/medicare-part-d-spending-by-drug"
        ),
        "scope": "National program spending and average unit cost by year",
    },
    "fda_orange_book": {
        "label": "FDA Orange Book (Therapeutic Equivalence)",
        "url": (
            "https://www.fda.gov/drugs/drug-approvals-and-databases/"
            "approved-drug-products-therapeutic-equivalence-evaluations-orange-book"
        ),
        "scope": "Therapeutically equivalent generic alternatives",
    },
    "cms_policy_corpus": {
        "label": "CMS Policy & Program Guidance",
        "url": "https://www.cms.gov/medicare/prescription-drug-coverage",
        "scope": "Part D benefit rules and program explanations",
    },
    "rxnorm_api": {
        "label": "RxNorm (NLM)",
        "url": "https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html",
        "scope": "Drug name normalization",
    },
    "rxnorm_cache": {
        "label": "RxNorm (local cache)",
        "url": "https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html",
        "scope": "Drug name normalization",
    },
}


def label_for_source_id(source_id: str) -> str:
    if source_id in SOURCE_CATALOG:
        return SOURCE_CATALOG[source_id]["label"]
    lowered = source_id.lower()
    if "spuf" in lowered:
        return SOURCE_CATALOG["cms_spuf_2026_q1"]["label"]
    if "spending" in lowered:
        return SOURCE_CATALOG["cms_part_d_spending"]["label"]
    return source_id


def url_for_source_id(source_id: str) -> str | None:
    if source_id in SOURCE_CATALOG:
        return SOURCE_CATALOG[source_id]["url"]
    lowered = source_id.lower()
    if "spuf" in lowered:
        return SOURCE_CATALOG["cms_spuf_2026_q1"]["url"]
    if "spending" in lowered:
        return SOURCE_CATALOG["cms_part_d_spending"]["url"]
    return None


def scope_for_source_id(source_id: str) -> str | None:
    if source_id in SOURCE_CATALOG:
        return SOURCE_CATALOG[source_id].get("scope")
    lowered = source_id.lower()
    if "spuf" in lowered:
        return SOURCE_CATALOG["cms_spuf_2026_q1"]["scope"]
    if "spending" in lowered:
        return SOURCE_CATALOG["cms_part_d_spending"]["scope"]
    return None


def drug_name_from_artifacts(tool_artifacts: dict[str, Any]) -> str | None:
    norm = tool_artifacts.get("normalize_drug")
    if not norm or norm.get("status") != "ok":
        return None
    data = norm.get("data") or {}
    if not isinstance(data, dict):
        return None
    selected = data.get("selected") or {}
    if selected.get("drug_name"):
        return str(selected["drug_name"])
    candidates = data.get("candidates") or []
    if candidates and candidates[0].get("drug_name"):
        return str(candidates[0]["drug_name"])
    return None


def formulary_citation_claim(data: dict[str, Any], drug_name: str | None = None) -> str:
    drug = drug_name.capitalize() if drug_name else "Drug"
    plan_key = data.get("plan_key", "plan")
    tier = data.get("tier")
    cs = data.get("cost_share") or {}
    copay = cs.get("copay")
    if tier is not None and copay is not None:
        return f"{drug} tier {tier} (${copay:.2f} copay) on plan {plan_key}"
    if tier is not None:
        return f"{drug} tier {tier} cost-sharing on plan {plan_key}"
    return f"{drug} formulary status on plan {plan_key}"


def trend_citation_claim(points: list[dict[str, Any]], drug_name: str | None = None) -> str:
    if not points:
        return "National Part D spending trend"
    first, last = points[0], points[-1]
    drug = drug_name.capitalize() if drug_name else "Drug"
    year_range = f"{first.get('year')}-{last.get('year')}"
    first_cost = first.get("avg_unit_cost")
    last_cost = last.get("avg_unit_cost")
    if first_cost is not None and last_cost is not None:
        return (
            f"{drug} national Part D avg unit cost {year_range} "
            f"(${float(first_cost):.2f}→${float(last_cost):.2f})"
        )
    return f"{drug} national Part D spending trend {year_range}"
