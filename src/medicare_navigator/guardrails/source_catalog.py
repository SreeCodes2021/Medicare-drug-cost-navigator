"""Human-readable labels and documentation URLs for data sources."""

from __future__ import annotations

from typing import Any

from medicare_navigator.tools.pharmacy_channels import channel_cost_bounds

SOURCE_CATALOG: dict[str, dict[str, str]] = {
    "cms_spuf_2026_q1": {
        "label": "CMS Part D Formulary & Pricing (SPUF)",
        "url": (
            "https://data.cms.gov/provider-summary-by-type-of-service/"
            "medicare-part-d-prescribers/quarterly-prescription-drug-plan-formulary-pharmacy-network-and-pricing-information"
        ),
        "scope": "Plan-specific tier, copay, and benefit phase",
    },
    "rxnorm_api": {
        "label": "RxNorm (NLM)",
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
    return source_id


def url_for_source_id(source_id: str) -> str | None:
    if source_id in SOURCE_CATALOG:
        return SOURCE_CATALOG[source_id]["url"]
    lowered = source_id.lower()
    if "spuf" in lowered:
        return SOURCE_CATALOG["cms_spuf_2026_q1"]["url"]
    return None


def scope_for_source_id(source_id: str) -> str | None:
    if source_id in SOURCE_CATALOG:
        return SOURCE_CATALOG[source_id].get("scope")
    lowered = source_id.lower()
    if "spuf" in lowered:
        return SOURCE_CATALOG["cms_spuf_2026_q1"]["scope"]
    return None


def drug_name_from_artifacts(tool_artifacts: dict[str, Any]) -> str | None:
    for name in ("estimate_drug_cost_all_channels", "estimate_drug_cost"):
        estimate = tool_artifacts.get(name)
        if not estimate or estimate.get("status") not in ("ok",):
            continue
        data = estimate.get("data") or {}
        if not isinstance(data, dict):
            continue
        drug = data.get("drug_name")
        if drug:
            return str(drug)
    return None


def formulary_citation_claim(data: dict[str, Any], drug_name: str | None = None) -> str:
    drug = drug_name.capitalize() if drug_name else "Drug"
    plan_key = data.get("plan_key", "plan")
    tiers = data.get("tiers_matched") or []
    tier = data.get("tier")
    if tier is not None and tier not in tiers:
        tiers = [tier, *tiers]
    channels = data.get("channels")
    if channels:
        cost_low, cost_high = channel_cost_bounds(channels)
    else:
        cost_low = data.get("cost_low")
        cost_high = data.get("cost_high")
    tier_label = f"tier {tiers[0]}" if len(tiers) == 1 else "formulary" if tiers else "formulary"
    if cost_low is not None and cost_high is not None:
        if cost_low == cost_high:
            return f"{drug} {tier_label} (${cost_low:.2f} estimate) on plan {plan_key}"
        return f"{drug} {tier_label} (${cost_low:.2f}–${cost_high:.2f} estimate) on plan {plan_key}"
    return f"{drug} {tier_label} status on plan {plan_key}"
