from medicare_navigator.guardrails.citations import (
    build_citations_from_artifacts,
    enrich_citations,
)
from medicare_navigator.models.citation import Citation


def _formulary_artifact():
    return {
        "status": "ok",
        "source_id": "cms_spuf_2026_q1",
        "as_of_date": "2026-01-15",
        "data": {
            "plan_key": "H1234-045",
            "plan_name": "Demo PDP",
            "tier": 2,
            "cost_share": {"copay": 15.0},
            "benefit_phase": "initial_coverage",
            "ytd_oop_spend": 0.0,
            "oop_threshold": 2100.0,
            "deductible": 615.0,
            "covered": True,
            "ytd_oop_spend_assumed": True,
        },
    }


def test_build_citations_includes_source_urls():
    artifacts = {"formulary_benefit_lookup": _formulary_artifact()}
    citations = build_citations_from_artifacts(artifacts)

    assert len(citations) == 1
    assert citations[0].url is not None
    assert "formulary" in citations[0].url.lower() or "spuf" in citations[0].source_label.lower()
    assert citations[0].source_label == "CMS Part D Formulary & Pricing (SPUF)"
    assert "Demo" not in citations[0].source_label


def test_lisinopril_cost_change_citations_are_distinct():
    artifacts = {
        "normalize_drug": {
            "status": "ok",
            "source_id": "rxnorm_cache",
            "as_of_date": "2026-01-15",
            "data": {
                "selected": {"drug_name": "lisinopril", "rxcui": "29046"},
            },
        },
        "formulary_benefit_lookup": {
            "status": "ok",
            "source_id": "cms_spuf_2026_q1",
            "as_of_date": "2026-01-15",
            "data": {
                "plan_key": "S5678-012",
                "plan_name": "Demo MA-PD",
                "tier": 1,
                "cost_share": {"copay": 2.0},
                "benefit_phase": "deductible",
                "ytd_oop_spend": 0.0,
                "oop_threshold": 2100.0,
                "deductible": 615.0,
                "covered": True,
                "ytd_oop_spend_assumed": True,
            },
        },
        "cost_trend_lookup": {
            "status": "ok",
            "source_id": "cms_part_d_spending",
            "as_of_date": "2026-01-15",
            "data": [
                {"year": 2022, "total_spend": 800_000_000, "avg_unit_cost": 0.08},
                {"year": 2025, "total_spend": 1_050_000_000, "avg_unit_cost": 0.12},
            ],
        },
    }

    citations = build_citations_from_artifacts(artifacts)

    assert len(citations) == 2
    assert citations[0].source_id != citations[1].source_id
    assert citations[0].source_label == "CMS Part D Formulary & Pricing (SPUF)"
    assert citations[1].source_label == "CMS Medicare Part D Drug Spending"
    assert "lisinopril" in citations[0].claim.lower()
    assert "lisinopril" in citations[1].claim.lower()
    assert "tier 1" in citations[0].claim.lower()
    assert "unit cost" in citations[1].claim.lower()
    assert citations[0].url != citations[1].url
    assert "spending-by-drug" in citations[1].url


def test_policy_citations_are_per_passage_with_urls():
    artifacts = {
        "policy_retrieval": {
            "status": "ok",
            "source_id": "cms_policy_corpus",
            "as_of_date": "2026-01-15",
            "data": [
                {
                    "passage_id": "cms_part_d_redesign_2026",
                    "text": "For CY 2026, the Part D annual out-of-pocket threshold is $2,100.",
                    "source_label": "CMS CY 2026 Part D Redesign Instructions",
                    "url": "https://www.cms.gov/newsroom/fact-sheets/final-cy-2026-part-d-redesign-program-instructions",
                },
                {
                    "passage_id": "ira_negotiated_prices",
                    "text": "The Inflation Reduction Act Medicare Drug Price Negotiation Program establishes Maximum Fair Prices.",
                    "source_label": "CMS Medicare Drug Price Negotiation Program",
                    "url": "https://www.cms.gov/medicare/medicare-drug-price-negotiation",
                },
            ],
        }
    }

    citations = build_citations_from_artifacts(artifacts)

    assert len(citations) == 2
    assert citations[0].claim == "CMS CY 2026 Part D Redesign Instructions"
    assert citations[0].url.endswith("final-cy-2026-part-d-redesign-program-instructions")
    assert citations[1].url.endswith("medicare-drug-price-negotiation")


def test_enrich_citations_adds_policy_url_for_matching_claim():
    artifacts = {
        "policy_retrieval": {
            "status": "ok",
            "source_id": "cms_policy_corpus",
            "as_of_date": "2026-01-15",
            "data": [
                {
                    "passage_id": "formulary_tier_explanation",
                    "text": "Part D plans place drugs on formulary tiers.",
                    "source_label": "CMS SPUF Methodology",
                    "url": "https://www.cms.gov/files/document/methodology-spuf-2025.pdf",
                }
            ],
        }
    }
    citations = [
        Citation(
            claim="Part D plans place drugs on formulary tiers.",
            source_id="cms_policy_corpus",
            as_of_date="2026-01-15",
            source_label="CMS Policy Corpus",
        )
    ]

    enriched = enrich_citations(citations, artifacts)

    assert enriched[0].url == "https://www.cms.gov/files/document/methodology-spuf-2025.pdf"
