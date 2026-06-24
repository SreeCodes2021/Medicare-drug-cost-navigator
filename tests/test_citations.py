from medicare_navigator.guardrails.citations import (
    apply_guardrails,
    build_citations_from_artifacts,
)


def _estimate_artifact(**data_overrides):
    data = {
        "plan_key": "H1234-045",
        "plan_name": "Demo PDP",
        "drug_name": "metformin",
        "rxcui": "6809",
        "tiers_matched": [2],
        "matched_ndc_count": 1,
        "same_tier": True,
        "days_supply": 30,
        "benefit_phase": "initial_coverage",
        "cost_low": 15.0,
        "cost_high": 15.0,
        "caveats": [],
        "covered": True,
    }
    data.update(data_overrides)
    return {
        "status": "ok",
        "source_id": "cms_spuf_2026_q1",
        "as_of_date": "2026-01-15",
        "message": None,
        "data": data,
    }


def test_build_citations_includes_source_urls():
    artifacts = {"estimate_drug_cost": _estimate_artifact()}
    citations = build_citations_from_artifacts(artifacts)

    assert len(citations) == 1
    assert citations[0].url is not None
    assert citations[0].source_label == "CMS Part D Formulary & Pricing (SPUF)"
    assert "metformin" in citations[0].claim.lower()


def test_build_citations_shows_cost_range_when_ndcs_differ():
    artifacts = {
        "estimate_drug_cost": _estimate_artifact(
            drug_name="lisinopril", cost_low=8.10, cost_high=14.40, matched_ndc_count=3
        )
    }
    citations = build_citations_from_artifacts(artifacts)
    assert len(citations) == 1
    assert "8.10" in citations[0].claim
    assert "14.40" in citations[0].claim


def test_apply_guardrails_force_appends_suppressed_message():
    """Bug 6: a hard-stop message must survive verbatim even if the LLM drops it."""
    message = "This plan's pharmacy data has been suppressed by CMS for this period..."
    artifacts = {
        "estimate_drug_cost": {
            "status": "suppressed",
            "source_id": "cms_spuf_2026_q1",
            "as_of_date": "2026-01-15",
            "message": message,
            "data": None,
        }
    }
    explanation, _citations, _errors = apply_guardrails(
        "Sorry, I can't help with that plan right now.", artifacts
    )
    assert message in explanation


def test_apply_guardrails_force_appends_caveats():
    artifacts = {"estimate_drug_cost": _estimate_artifact(caveats=["COINSURANCE NOT CALCULATED — CONTACT INSURER. details"])}
    explanation, _citations, _errors = apply_guardrails(
        "Metformin costs $15.00 on this plan.", artifacts
    )
    assert "COINSURANCE NOT CALCULATED" in explanation


def test_build_citations_for_plan_not_found():
    artifacts = {
        "estimate_drug_cost": {
            "status": "not_found",
            "source_id": "cms_spuf_2026_q1",
            "as_of_date": "2026-01-15",
            "message": "Plan 'S5678-012' not found.",
            "data": None,
        }
    }
    citations = build_citations_from_artifacts(artifacts)

    assert len(citations) == 1
    assert "S5678-012" in citations[0].claim
    assert citations[0].source_label == "CMS Part D Formulary & Pricing (SPUF)"
    assert citations[0].url is not None


def test_build_citations_for_lookup_plan_not_found():
    artifacts = {
        "lookup_plan": {
            "status": "not_found",
            "source_id": "cms_spuf_2026_q1",
            "as_of_date": "2026-01-15",
            "message": "Plan 'S5678-012' not found.",
            "data": None,
        }
    }
    citations = build_citations_from_artifacts(artifacts)

    assert len(citations) == 1
    assert citations[0].claim == "Plan 'S5678-012' not found."


def test_apply_guardrails_flags_untraceable_dollar_amount():
    artifacts = {"estimate_drug_cost": _estimate_artifact(cost_low=15.0, cost_high=15.0)}
    _explanation, _citations, errors = apply_guardrails(
        "Metformin costs $999.99 on this plan.", artifacts
    )
    assert any("999.99" in e for e in errors)
