"""Tests for channel parity helpers."""

from medicare_navigator.guardrails.channel_parity import (
    channel_coverage_note,
    channel_contrast_sentence_for_estimate,
    channel_wording_for_channels,
    cost_sentence_for_estimate,
    is_mail_retail_contrast_question,
    prose_channel_overclaim_warnings,
    prose_false_unavailable_warnings,
    prose_tied_lowest_warnings,
    repair_false_unavailable_prose,
    repair_missing_mail_retail_contrast_in_prose,
    repair_missing_tier_in_prose,
    summarize_channel_coverage,
    summarize_channels_dict,
)


def test_summarize_channels_dict_partial_coverage():
    channels = {
        "preferred_retail": {"cost_low": 5.0, "cost_high": 5.0},
        "standard_retail": {"cost_low": 13.0, "cost_high": 13.0},
        "preferred_mail": {"cost_low": None, "cost_high": None},
        "standard_mail": {"cost_low": None, "cost_high": None},
    }
    summary = summarize_channels_dict(channels)
    assert summary["priced_channels"] == ["preferred_retail", "standard_retail"]
    assert summary["missing_channels"] == ["preferred_mail", "standard_mail"]


def test_channel_wording_single_priced_channel():
    channels = {
        "preferred_retail": {"cost_low": None},
        "standard_retail": {"cost_low": 0.0, "cost_high": 0.0},
        "preferred_mail": {"cost_low": None},
        "standard_mail": {"cost_low": None},
    }
    note = channel_wording_for_channels(channels)
    assert "Standard retail only" in note
    assert "no matching estimate" in note


def test_channel_coverage_note_lists_missing_channels():
    coverage = summarize_channel_coverage(
        [
            {
                "plan_key": "S9999-004",
                "channels": {
                    "preferred_retail": {"cost_low": None},
                    "standard_retail": {"cost_low": 5.0, "cost_high": 5.0},
                    "preferred_mail": {"cost_low": None},
                    "standard_mail": {"cost_low": None},
                },
            }
        ]
    )
    note = channel_coverage_note(coverage)
    assert note is not None
    assert "S9999-004" in note
    assert "Standard retail" in note


def test_prose_channel_overclaim_warnings():
    coverage = summarize_channel_coverage(
        [
            {
                "plan_key": "H2802-063",
                "channels": {
                    "preferred_retail": {"cost_low": None},
                    "standard_retail": {"cost_low": 0.0, "cost_high": 0.0},
                    "preferred_mail": {"cost_low": None},
                    "standard_mail": {"cost_low": None},
                },
            }
        ]
    )
    warnings = prose_channel_overclaim_warnings(
        "Cost is $0 across all CMS pharmacy channels.",
        coverage,
    )
    assert warnings


def test_prose_false_unavailable_warnings_when_priced_channels_exist():
    coverage = summarize_channel_coverage(
        [
            {
                "plan_key": "H2802-063",
                "channels": {
                    "preferred_retail": {"cost_low": None},
                    "standard_retail": {"cost_low": 0.0, "cost_high": 0.0},
                    "preferred_mail": {"cost_low": None},
                    "standard_mail": {"cost_low": None},
                },
            }
        ]
    )
    warnings = prose_false_unavailable_warnings(
        "H2802-063: metformin is covered but the estimate is not available on the pricing table.",
        coverage,
    )
    assert warnings
    assert "H2802-063" in warnings[0]


def test_prose_false_unavailable_skips_when_dollar_in_lead():
    coverage = summarize_channel_coverage(
        [
            {
                "plan_key": "H2802-063",
                "channels": {
                    "preferred_retail": {"cost_low": None},
                    "standard_retail": {"cost_low": 0.0, "cost_high": 0.0},
                    "preferred_mail": {"cost_low": None},
                    "standard_mail": {"cost_low": None},
                },
            }
        ]
    )
    warnings = prose_false_unavailable_warnings(
        "H2802-063: metformin is $0.00 at standard retail only.",
        coverage,
    )
    assert warnings == []


def test_prose_tied_lowest_warnings_when_only_one_plan_named():
    coverage = summarize_channel_coverage(
        [
            {
                "plan_key": "H2802-063",
                "channels": {
                    "preferred_retail": {"cost_low": None},
                    "standard_retail": {"cost_low": 0.0, "cost_high": 0.0},
                    "preferred_mail": {"cost_low": None},
                    "standard_mail": {"cost_low": None},
                },
            },
            {
                "plan_key": "H5216-366",
                "channels": {
                    "preferred_retail": {"cost_low": None},
                    "standard_retail": {"cost_low": 0.0, "cost_high": 0.0},
                    "preferred_mail": {"cost_low": 0.0, "cost_high": 0.0},
                    "standard_mail": {"cost_low": 0.0, "cost_high": 0.0},
                },
            },
        ]
    )
    warnings = prose_tied_lowest_warnings(
        "The lowest estimated cost is $0.00 on H5216-366.",
        coverage,
    )
    assert warnings
    assert "H2802-063" in warnings[0]


def test_cost_sentence_for_estimate_zero_dollar_standard_retail():
    sentence = cost_sentence_for_estimate(
        {
            "drug_name": "metformin",
            "dosage": "500mg",
            "days_supply": 30,
            "plan_key": "H1045-057",
            "tier": 1,
            "channels": {
                "preferred_retail": {"cost_low": None},
                "standard_retail": {"cost_low": 0.0, "cost_high": 0.0},
                "preferred_mail": {"cost_low": None},
                "standard_mail": {"cost_low": None},
            },
        }
    )
    assert sentence is not None
    assert "$0.00" in sentence
    assert "H1045-057" in sentence
    assert "Tier 1" in sentence


def test_cost_sentence_for_estimate_skips_tier_for_insulin_cap():
    sentence = cost_sentence_for_estimate(
        {
            "drug_name": "lantus",
            "days_supply": 30,
            "plan_key": "S9999-001",
            "tier": 3,
            "benefit_phase": "insulin_cap",
            "channels": {
                "preferred_retail": {"cost_low": 35.0, "cost_high": 35.0},
            },
        }
    )
    assert sentence is not None
    assert "Tier 3" not in sentence


def test_repair_missing_tier_in_prose_prepends_grounded_tier():
    est = {
        "drug_name": "metformin",
        "dosage": "500mg",
        "tier": 2,
        "plan_key": "H8888-001",
        "channels": {"standard_retail": {"cost_low": 10.0, "cost_high": 10.0}},
    }
    prose = "Metformin is estimated at $10.00–$15.00 for a 30-day supply."
    repaired = repair_missing_tier_in_prose(prose, [est])
    assert "Tier 2" in repaired
    assert repaired.startswith("Metformin is a Tier 2")


def test_repair_missing_tier_in_prose_noop_when_tier_present():
    est = {
        "drug_name": "omeprazole",
        "dosage": "20mg",
        "tier": 3,
        "channels": {"preferred_retail": {"cost_low": 40.0, "cost_high": 40.0}},
    }
    prose = "Omeprazole is Tier 3 and costs about $40.00."
    assert repair_missing_tier_in_prose(prose, [est]) == prose


def test_repair_false_unavailable_prepends_zero_dollar_lead():
    est = {
        "drug_name": "metformin",
        "dosage": "500mg",
        "days_supply": 30,
        "plan_key": "H1045-057",
        "channels": {
            "preferred_retail": {"cost_low": None},
            "standard_retail": {"cost_low": 0.0, "cost_high": 0.0},
            "preferred_mail": {"cost_low": None},
            "standard_mail": {"cost_low": None},
        },
    }
    bad = (
        "For metformin 500 mg on plan H1045-057, the tool can't calculate a dollar "
        "out-of-pocket estimate."
    )
    repaired = repair_false_unavailable_prose(bad, [est])
    assert repaired.startswith("Metformin")
    assert "$0.00" in repaired
    assert "can't calculate" in repaired


def test_prose_false_unavailable_catches_cant_calculate_phrase():
    coverage = summarize_channel_coverage(
        [
            {
                "plan_key": "H1045-057",
                "channels": {
                    "preferred_retail": {"cost_low": None},
                    "standard_retail": {"cost_low": 0.0, "cost_high": 0.0},
                    "preferred_mail": {"cost_low": None},
                    "standard_mail": {"cost_low": None},
                },
            }
        ]
    )
    warnings = prose_false_unavailable_warnings(
        "H1045-057: can't calculate a dollar estimate for this fill.",
        coverage,
    )
    assert warnings


def test_is_mail_retail_contrast_question():
    assert is_mail_retail_contrast_question(
        "How does mail order compare to retail for metformin?"
    )
    assert not is_mail_retail_contrast_question("Preferred retail cost for metformin")


def test_channel_contrast_sentence_lists_mail_and_retail():
    est = {
        "drug_name": "metformin",
        "dosage": "500mg",
        "days_supply": 30,
        "plan_key": "S9999-001",
        "tier": 1,
        "channels": {
            "preferred_retail": {"cost_low": 5.0, "cost_high": 5.0},
            "standard_retail": {"cost_low": 15.0, "cost_high": 15.0},
            "preferred_mail": {"cost_low": 3.0, "cost_high": 3.0},
            "standard_mail": {"cost_low": 8.0, "cost_high": 8.0},
        },
    }
    sentence = channel_contrast_sentence_for_estimate(est)
    assert sentence is not None
    assert "mail" in sentence.lower()
    assert "retail" in sentence.lower()
    assert "$3.00" in sentence
    assert "$5.00" in sentence


def test_repair_missing_mail_retail_contrast_prepends_breakdown():
    est = {
        "drug_name": "metformin",
        "dosage": "500mg",
        "days_supply": 30,
        "plan_key": "S9999-001",
        "tier": 1,
        "channels": {
            "preferred_retail": {"cost_low": 5.0, "cost_high": 5.0},
            "standard_retail": {"cost_low": 15.0, "cost_high": 15.0},
            "preferred_mail": {"cost_low": 3.0, "cost_high": 3.0},
            "standard_mail": {"cost_low": 8.0, "cost_high": 8.0},
        },
    }
    prose = (
        "Metformin 500mg on S9999-001 (Tier 1) is estimated at $3.00–$15.00 for a "
        "30-day fill, depending on pharmacy channel."
    )
    question = "How does mail order compare to retail for metformin 500mg on S9999-001?"
    repaired = repair_missing_mail_retail_contrast_in_prose(prose, [est], question)
    assert repaired.startswith("For metformin")
    assert "mail" in repaired.lower()
    assert "depending on pharmacy channel" in repaired


def test_repair_missing_mail_retail_contrast_noop_for_retail_only_ask():
    est = {
        "drug_name": "metformin",
        "dosage": "500mg",
        "plan_key": "S9999-001",
        "channels": {
            "preferred_retail": {"cost_low": 5.0, "cost_high": 5.0},
            "preferred_mail": {"cost_low": 3.0, "cost_high": 3.0},
        },
    }
    prose = "The preferred retail cost is $5.00 for a 30-day fill."
    repaired = repair_missing_mail_retail_contrast_in_prose(
        prose,
        [est],
        "What's the preferred retail cost for metformin 500mg on S9999-001?",
    )
    assert repaired == prose
