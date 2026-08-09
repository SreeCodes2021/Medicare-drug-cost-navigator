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


def _all_channels_artifact(**data_overrides):
    data = {
        "plan_key": "H1234-045",
        "plan_name": "Demo PDP",
        "drug_name": "lovastatin",
        "rxcui": "6472",
        "covered": True,
        "days_supply": 30,
        "ytd_oop_spend": 0,
        "tier": 1,
        "tiers_matched": [1],
        "benefit_phase": "pre_deductible",
        "effective_phase": "initial_coverage",
        "channels": {
            "preferred_retail": {"cost_low": 5.0, "cost_high": 5.0, "coinsurance": False},
            "standard_retail": {"cost_low": 13.0, "cost_high": 13.0, "coinsurance": False},
            "preferred_mail": {"cost_low": None, "cost_high": None, "coinsurance": False},
            "standard_mail": {"cost_low": None, "cost_high": None, "coinsurance": False},
        },
        "caveats": [],
    }
    data.update(data_overrides)
    return {
        "status": "ok",
        "source_id": "cms_spuf_2026_q1",
        "as_of_date": "2026-01-15",
        "message": None,
        "data": data,
    }


def test_build_citations_all_channels_cost_range():
    artifacts = {"estimate_drug_cost_all_channels": _all_channels_artifact()}
    citations = build_citations_from_artifacts(artifacts)
    assert len(citations) == 1
    assert "5.00" in citations[0].claim
    assert "13.00" in citations[0].claim


def test_apply_guardrails_flags_all_channels_overclaim():
    artifacts = {
        "estimate_drug_cost_all_channels__calls": [_all_channels_artifact()],
    }
    explanation, _citations, errors = apply_guardrails(
        "Lovastatin is $5.00 across all CMS pharmacy channels.",
        artifacts,
    )
    assert any("all pharmacy channels" in e.lower() for e in errors)
    assert "Standard retail" in explanation or "no matching estimate" in explanation


def test_apply_guardrails_appends_channel_coverage_note_for_partial_data():
    artifacts = {
        "estimate_drug_cost_all_channels__calls": [_all_channels_artifact()],
    }
    explanation, _citations, errors = apply_guardrails(
        "Lovastatin is estimated at $5.00–$13.00 depending on pharmacy channel.",
        artifacts,
    )
    assert not any("all pharmacy channels" in e.lower() for e in errors)
    assert "no matching estimate" not in explanation.lower()


def test_apply_guardrails_skips_duplicate_channel_note_when_prose_covers_gaps():
    artifacts = {
        "estimate_drug_cost_all_channels__calls": [_all_channels_artifact()],
    }
    explanation, _citations, errors = apply_guardrails(
        (
            "Lovastatin is $5.00 at preferred retail and $13.00 at standard retail; "
            "CMS has no matching estimate for mail-order channels."
        ),
        artifacts,
    )
    assert errors == []
    assert explanation.count("no matching estimate") == 1


def _compare_partial_channels_artifacts():
    return {
        "estimate_drug_cost_all_channels__calls": [
            _all_channels_artifact(
                plan_key="H2802-063",
                plan_name="Giveback AR-3",
                channels={
                    "preferred_retail": {"cost_low": None, "cost_high": None, "coinsurance": False},
                    "standard_retail": {"cost_low": 0.0, "cost_high": 0.0, "coinsurance": False},
                    "preferred_mail": {"cost_low": None, "cost_high": None, "coinsurance": False},
                    "standard_mail": {"cost_low": None, "cost_high": None, "coinsurance": False},
                },
            ),
            _all_channels_artifact(
                plan_key="H5216-366",
                plan_name="HumanaChoice C-SNP",
                drug_name="metformin",
                channels={
                    "preferred_retail": {"cost_low": None, "cost_high": None, "coinsurance": False},
                    "standard_retail": {"cost_low": 0.0, "cost_high": 0.0, "coinsurance": False},
                    "preferred_mail": {"cost_low": 0.0, "cost_high": 0.0, "coinsurance": False},
                    "standard_mail": {"cost_low": 0.0, "cost_high": 0.0, "coinsurance": False},
                },
            ),
        ],
    }


def test_apply_guardrails_flags_false_not_available_with_priced_channels():
    artifacts = _compare_partial_channels_artifacts()
    _explanation, _citations, errors = apply_guardrails(
        (
            "H2802-063: covered but estimate not available. "
            "H5216-366: $0.00. Lowest cost is H5216-366."
        ),
        artifacts,
    )
    assert any("H2802-063" in e and "priced" in e for e in errors)


def test_apply_guardrails_flags_sole_lowest_when_plans_tie():
    artifacts = _compare_partial_channels_artifacts()
    _explanation, _citations, errors = apply_guardrails(
        "The lowest estimated cost is $0.00 on H5216-366.",
        artifacts,
    )
    assert any("tie" in e.lower() or "H2802-063" in e for e in errors)


def test_apply_guardrails_flags_alternatives_without_clinician_deferral():
    artifacts = {"lookup_plan": _estimate_artifact()}
    _explanation, _citations, errors = apply_guardrails(
        (
            "Yes, lower-cost alternatives to Januvia include metformin and glipizide "
            "on this plan."
        ),
        artifacts,
    )
    assert any("doctor" in e.lower() or "pharmacist" in e.lower() for e in errors)


def test_apply_guardrails_allows_alternatives_with_clinician_deferral():
    artifacts = {"lookup_plan": _estimate_artifact()}
    _explanation, _citations, errors = apply_guardrails(
        (
            "Discuss any substitute with your doctor or pharmacist first. "
            "I can estimate metformin or glipizide if you name a strength."
        ),
        artifacts,
    )
    assert errors == []


def test_apply_guardrails_allows_all_channel_dollar_amounts():
    artifacts = {"estimate_drug_cost_all_channels": _all_channels_artifact()}
    _explanation, _citations, errors = apply_guardrails(
        "Lovastatin is estimated at $5.00–$13.00 depending on pharmacy channel.",
        artifacts,
    )
    assert errors == []


def test_apply_guardrails_flags_untraceable_channel_amount():
    artifacts = {"estimate_drug_cost_all_channels": _all_channels_artifact()}
    _explanation, _citations, errors = apply_guardrails(
        "Lovastatin costs $99.00 on this plan.", artifacts
    )
    assert any("99.00" in e for e in errors)


def test_estimate_from_artifact_all_channels():
    from medicare_navigator.guardrails.citations import (
        channel_estimate_from_artifact,
        estimate_from_artifact,
    )

    artifacts = {"estimate_drug_cost_all_channels": _all_channels_artifact()}
    estimate = estimate_from_artifact(artifacts)
    assert estimate is not None
    assert estimate.cost_low == 5.0
    assert estimate.cost_high == 13.0
    channel = channel_estimate_from_artifact(artifacts)
    assert channel is not None
    assert channel.channels["preferred_retail"].cost_low == 5.0


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


def test_apply_guardrails_skips_bug2_caveat_in_explanation_prose():
    from medicare_navigator.tools.disclaimers import BUG2_CAVEAT

    artifacts = {
        "estimate_drug_cost_all_channels": _all_channels_artifact(caveats=[BUG2_CAVEAT]),
    }
    explanation, _citations, errors = apply_guardrails(
        (
            "Lovastatin is $5.00 at preferred retail and $13.00 at standard retail for a 30-day fill."
            f"\n\n{BUG2_CAVEAT}"
        ),
        artifacts,
    )
    assert errors == []
    assert BUG2_CAVEAT not in explanation
    assert "$5.00" in explanation


def test_apply_guardrails_strips_llm_disclaimer_before_appending_canonical():
    artifacts = {"estimate_drug_cost": _estimate_artifact()}
    paraphrased = (
        "Metformin costs $15.00 on this plan.\n\n"
        "General disclaimer: Figures are government reference data for the current quarter."
    )
    explanation, _citations, _errors = apply_guardrails(paraphrased, artifacts)
    assert explanation.count("Disclaimer:") == 1
    assert "General disclaimer:" not in explanation


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


def test_apply_guardrails_allows_dollar_amount_missing_trailing_zero():
    """$31.5 and $31.50 are the same number — a naive string-exact allowlist (built from
    f"{value:.2f}") would reject the former as "untraceable" even though it matches cost_low."""
    artifacts = {"estimate_drug_cost": _estimate_artifact(cost_low=31.5, cost_high=31.5)}
    _explanation, _citations, errors = apply_guardrails(
        "Omeprazole costs $31.5 on this plan.", artifacts
    )
    assert errors == []
