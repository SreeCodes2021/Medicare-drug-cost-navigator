"""Unit tests for the is_insulin() name/ingredient allowlist, independent of any DB
fixture — this check runs before/around RxNorm resolution in estimate_drug_cost.py."""

from medicare_navigator.tools.insulin import is_insulin
from medicare_navigator.agent.insulin_requests import (
    INSULIN_INTENT_CHANNEL_CONTRAST,
    INSULIN_INTENT_MULTI_PLAN_COMPARE,
    INSULIN_INTENT_POLICY_CATASTROPHIC,
    INSULIN_INTENT_POLICY_CEILING,
    INSULIN_INTENT_POLICY_DEDUCTIBLE,
    INSULIN_INTENT_POLICY_IRA,
    INSULIN_INTENT_TIER_LOOKUP,
    _extract_ytd_oop_spend,
    message_names_non_insulin_cost_drugs,
    resolve_insulin_request,
)


def test_lyumjev_is_recognized_as_insulin():
    """Gap found in live CMS formulary data: Lyumjev (insulin lispro-aabc) was on-
    formulary under this exact brand name but missing from the original allowlist."""
    assert is_insulin("Lyumjev") is True


def test_soliqua_is_recognized_as_insulin():
    """GLP-1/insulin combination product, billed as a single capped insulin product."""
    assert is_insulin("Soliqua") is True


def test_xultophy_is_recognized_as_insulin():
    """GLP-1/insulin combination product, billed as a single capped insulin product."""
    assert is_insulin("Xultophy") is True


def test_case_and_whitespace_insensitive():
    assert is_insulin("  LYUMJEV  ") is True
    assert is_insulin("soliqua") is True


def test_non_insulin_drug_is_not_flagged():
    assert is_insulin("metformin") is False
    assert is_insulin(None, "metformin") is False


def test_message_names_non_insulin_cost_drugs_for_mixed_basket():
    assert message_names_non_insulin_cost_drugs("metformin and lantus on S9999-001")
    assert message_names_non_insulin_cost_drugs("warfarin 5mg and lantus on S9999-001")
    assert not message_names_non_insulin_cost_drugs("lantus and humalog on S9999-001")


def test_ingredient_only_match():
    assert is_insulin(None, "insulin glargine") is True


def test_rezvoglar_is_recognized_as_insulin():
    assert is_insulin("Rezvoglar") is True


def test_afrezza_is_recognized_as_insulin():
    assert is_insulin("Afrezza") is True


def test_insulin_lispro_aabc_ingredient_match():
    assert is_insulin(None, "insulin lispro-aabc") is True


def test_insulin_request_extracts_each_named_product_and_inputs():
    request = resolve_insulin_request(
        "Lantus and Humalog together on S9999-001 for 90 days after $2200 YTD"
    )
    assert request is not None
    assert request.products == ("lantus", "humalog")
    assert request.plan_key == "S9999-001"
    assert request.days_supply == 90
    assert request.ytd_oop_spend == 2200


def test_extract_ytd_oop_spend_handles_zero_and_spent_ytd_phrases():
    assert (
        _extract_ytd_oop_spend(
            "Omeprazole 20mg and lantus on S9999-001 at $0 YTD",
            filter_ytd_oop_spend=None,
        )
        == 0.0
    )
    assert (
        _extract_ytd_oop_spend(
            "At $0 YTD spend, what would omeprazole 20mg and Lantus cost on S9999-001?",
            filter_ytd_oop_spend=None,
        )
        == 0.0
    )
    assert (
        _extract_ytd_oop_spend(
            "With zero dollars spent YTD, what would omeprazole 20mg cost?",
            filter_ytd_oop_spend=None,
        )
        == 0.0
    )


def test_generic_insulin_policy_is_not_a_formulary_product():
    request = resolve_insulin_request(
        "Is insulin always exactly $35 on S9999-001, or can it be lower?"
    )
    assert request is not None
    assert request.products == ()
    assert request.is_policy_question is True


def test_insulin_request_recognizes_preferred_mail_order_channel():
    request = resolve_insulin_request(
        "What is the mail-order preferred price for Lantus on S9999-001?"
    )
    assert request is not None
    assert request.pharmacy_channel == "preferred_mail"


def test_insulin_request_detects_named_product_policy_ceiling():
    request = resolve_insulin_request(
        "Is insulin always $35 per month on plan S9999-001 for Lantus?"
    )
    assert request is not None
    assert request.products == ("lantus",)
    assert request.intent == INSULIN_INTENT_POLICY_CEILING


def test_insulin_request_detects_policy_ceiling_could_it_be_lower_wording():
    request = resolve_insulin_request(
        "On plan S9999-001, is Humalog always capped at $35 or could it be lower?"
    )
    assert request is not None
    assert request.intent == INSULIN_INTENT_POLICY_CEILING


def test_insulin_request_detects_deductible_policy_intent():
    request = resolve_insulin_request(
        "Does the Part D deductible apply to Lantus on plan S9999-001?"
    )
    assert request is not None
    assert request.intent == INSULIN_INTENT_POLICY_DEDUCTIBLE


def test_insulin_request_detects_ira_policy_intent():
    request = resolve_insulin_request(
        "Why is my Lantus estimate $35 on plan S9999-001?"
    )
    assert request is not None
    assert request.intent == INSULIN_INTENT_POLICY_IRA


def test_insulin_request_parses_catastrophic_oop_max_without_explicit_ytd():
    request = resolve_insulin_request(
        "I'm in catastrophic coverage — what is Lantus on plan S9999-001? "
        "Assume I've met my $2100 annual OOP max."
    )
    assert request is not None
    assert request.intent == INSULIN_INTENT_POLICY_CATASTROPHIC
    assert request.ytd_oop_spend == 2100.0


def test_insulin_request_detects_tier_lookup_intent():
    request = resolve_insulin_request(
        "What formulary tier is Lantus on plan S9999-001?"
    )
    assert request is not None
    assert request.intent == INSULIN_INTENT_TIER_LOOKUP


def test_insulin_request_detects_channel_contrast_intent():
    request = resolve_insulin_request(
        "For plan S9999-001, how does mail-order cost compare to retail for Lantus?"
    )
    assert request is not None
    assert request.intent == INSULIN_INTENT_CHANNEL_CONTRAST
    assert request.pharmacy_channel is None


def test_insulin_request_tier_compare_beats_multi_plan_intent():
    request = resolve_insulin_request(
        "Compare the formulary tier for Lantus on plan S9999-001 versus plan H8888-001."
    )
    assert request is not None
    assert request.intent == INSULIN_INTENT_TIER_LOOKUP


def test_insulin_request_detects_multi_plan_compare():
    request = resolve_insulin_request(
        "Compare Lantus costs between plans S9999-001 and H8888-001."
    )
    assert request is not None
    assert request.intent == INSULIN_INTENT_MULTI_PLAN_COMPARE
    assert request.plan_keys == ("S9999-001", "H8888-001")
