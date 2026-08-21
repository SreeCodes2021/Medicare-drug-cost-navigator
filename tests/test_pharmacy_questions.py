import pytest

from medicare_navigator.models.response import PharmacyResult
from medicare_navigator.agent.pharmacy_questions import (
    _format_pharmacy_distance_suffix,
    _pharmacy_list_sentence,
    _pharmacy_results_header,
    build_find_pharmacies_session_call,
    extract_zip,
    is_nearby_pharmacy_question,
    is_plan_coverage_question,
    is_preferred_pharmacy_question,
    resolve_nearby_pharmacy_question,
    resolve_pharmacy_cost_question,
    resolve_pharmacy_radius_follow_up,
    resolve_plan_coverage_question,
    resolve_plan_pharmacy_match_question,
    resolve_preferred_pharmacy_question,
)
from tests.spuf_fixture import (
    PLAN_FL_MAPD,
    PLAN_FL_MAPD_MOOP,
    PLAN_FL_PARTIAL_CHANNELS,
    PLAN_FL_PDP,
)


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


# --- ZIP extraction -----------------------------------------------------------------


def test_extract_zip_keyword_anchored():
    assert extract_zip("I live in zip 32801") == "32801"
    assert extract_zip("my zip code is 32801") == "32801"
    assert extract_zip("zip: 32801, plan S9999-001") == "32801"


def test_extract_zip_bare_fallback_requires_zip_keyword():
    assert extract_zip("what about 32801 near me") is None


def test_extract_zip_live_in_without_zip_keyword():
    assert extract_zip("I live in 72719. What are the pharmacies available near me?") == "72719"
    assert extract_zip("what are the pharmacies available near me? I live in 72719") == "72719"
    assert extract_zip("I'm in 32801") == "32801"


def test_extract_zip_no_zip_mentioned():
    assert extract_zip("what are the preferred pharmacies for my zip and my plan?") is None


def test_extract_zip_bare_digits_ignored_without_zip_context():
    """A bare 5-digit number elsewhere in the message (e.g. a plan ID fragment) must not
    be mistaken for a ZIP when the word 'zip' never appears."""
    assert extract_zip("how much does metformin cost, order #12345?") is None


# --- intent predicates ----------------------------------------------------------------


def test_is_preferred_pharmacy_question():
    assert is_preferred_pharmacy_question("what are my preferred pharmacies?")
    assert not is_preferred_pharmacy_question("what pharmacies are near me?")


def test_is_nearby_pharmacy_question():
    assert is_nearby_pharmacy_question("what pharmacies are near me?")
    assert is_nearby_pharmacy_question("pharmacies close to my zip 32801")
    assert not is_nearby_pharmacy_question("what are my preferred pharmacies?")


def test_is_nearby_pharmacy_question_matches_nearby_as_one_word():
    """'nearby' (single token) must match — \\bnear\\b alone doesn't, since there's no word
    boundary between 'near' and 'by'. A live LLM run caught this: 'any pharmacies nearby?'
    silently skipped the deterministic resolver and fell through to the general agent loop."""
    assert is_nearby_pharmacy_question("I live in zip 32801, any pharmacies nearby?")


def test_is_nearby_pharmacy_question_matches_misspelled_pharmacies():
    """A typo'd "pharamacies" (extra 'a') doesn't contain the literal 'pharmac' substring the
    regex requires, so plain regex matching misses it silently — same failure shape as the
    "nearby" one-word gap above. Caught from a live user report combining ZIP + drug + typo."""
    assert is_nearby_pharmacy_question(
        "I need to take lovastatin 40mg and I live in 72719 what plans over this "
        "in near by pharamacies?"
    )


def test_is_preferred_pharmacy_question_matches_misspelled_pharmacy():
    assert is_preferred_pharmacy_question("what are my preferred pharamacy options?")


def test_is_nearby_pharmacy_question_matches_in_my_zip_and_pharmacies_in_zip():
    assert is_nearby_pharmacy_question(
        "I live in 72712. What are the pharmacies available in my zip?"
    )
    assert is_nearby_pharmacy_question("Give me list of plans which has pharmacies in 72712")
    assert not is_nearby_pharmacy_question("how much does metformin cost at the pharmacy in 32801?")


def test_extract_zip_pharmacies_in_bare_zip_digits():
    assert extract_zip("Give me list of plans which has pharmacies in 72712") == "72712"


# --- Q1: resolve_preferred_pharmacy_question -------------------------------------------


def test_preferred_pharmacy_defers_when_not_a_pharmacy_question():
    assert resolve_preferred_pharmacy_question("how much does metformin cost?") is None


def test_preferred_pharmacy_missing_zip_asks_for_it():
    resolved = resolve_preferred_pharmacy_question(
        f"what are the preferred pharmacies for plan {PLAN_FL_PDP}?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "needs_clarification"
    assert "zip" in explanation.lower()
    assert artifacts == {}
    assert tools == []


def test_preferred_pharmacy_missing_plan_asks_for_it():
    resolved = resolve_preferred_pharmacy_question(
        "what are the preferred pharmacies for zip 32801?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "needs_clarification"
    assert "plan" in explanation.lower()


def test_preferred_pharmacy_uses_filter_plan_id_when_message_has_none():
    resolved = resolve_preferred_pharmacy_question(
        "what are my preferred pharmacies for zip 32801?",
        filter_plan_id=PLAN_FL_PDP,
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "Icon Pharmacy" in explanation
    assert "Angels Pharmacy" not in explanation  # standard, not preferred
    assert tools == ["find_pharmacies"]
    assert artifacts["find_pharmacies"]["status"] == "ok"


def test_preferred_pharmacy_ok_with_explicit_plan_in_message():
    resolved = resolve_preferred_pharmacy_question(
        f"what are the preferred pharmacies for my zip 32801 and plan {PLAN_FL_PDP}?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "Icon Pharmacy" in explanation


def test_preferred_pharmacy_no_results_in_radius_still_ok_status():
    resolved = resolve_preferred_pharmacy_question(
        f"what are the preferred pharmacies for zip 90001 and plan {PLAN_FL_PDP}?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "no pharmacies" in explanation.lower()


# --- Q3: resolve_nearby_pharmacy_question -----------------------------------------------


def test_nearby_pharmacy_defers_when_not_a_pharmacy_question():
    assert resolve_nearby_pharmacy_question("how much does metformin cost?") is None


def test_nearby_pharmacy_defers_to_preferred_resolver_when_both_match():
    assert resolve_nearby_pharmacy_question("what is my nearest preferred pharmacy?") is None


def test_nearby_pharmacy_missing_zip_asks_for_it():
    resolved = resolve_nearby_pharmacy_question("what pharmacies are near me?")
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "needs_clarification"
    assert "zip" in explanation.lower()


def test_nearby_pharmacy_ok_no_plan_needed():
    resolved = resolve_nearby_pharmacy_question(
        "what pharmacies are near me? I live in zip 32801."
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "Icon Pharmacy" in explanation
    # plan-agnostic: a standard-only-network pharmacy still shows up
    assert "Angels Pharmacy" in explanation


def test_nearby_pharmacy_ok_with_live_in_phrasing_without_zip_keyword():
    resolved = resolve_nearby_pharmacy_question(
        "I live in 72719. What are the pharmacies available near me?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "72719" in explanation
    assert "Icon Pharmacy" not in explanation


def test_nearby_pharmacy_ok_with_in_my_zip_phrasing():
    resolved = resolve_nearby_pharmacy_question(
        "I live in 32801. What are the pharmacies available in my zip?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "32801" in explanation
    assert "Icon Pharmacy" in explanation
    assert tools == ["find_pharmacies"]


def test_nearby_pharmacy_ok_with_pharmacies_in_bare_zip():
    resolved = resolve_nearby_pharmacy_question(
        "Give me list of plans which has pharmacies in 72712"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "72712" in explanation
    assert tools == ["find_pharmacies"]
    assert "can't list plans" not in explanation.lower()
    assert "cannot list plans" not in explanation.lower()


def test_nearby_pharmacy_scopes_to_named_plan_without_preferred_wording():
    """A plan named in a plain 'pharmacies near' ask (no 'preferred' wording) must still
    narrow to that plan's network, not silently fall back to a plan-agnostic scan."""
    resolved = resolve_nearby_pharmacy_question(
        f"what pharmacies are near zip 32801 for plan {PLAN_FL_MAPD}?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert PLAN_FL_MAPD in explanation
    assert "Icon Pharmacy" in explanation  # H8888-001's only networked pharmacy
    assert "Angels Pharmacy" not in explanation  # not in H8888-001's network


def test_nearby_pharmacy_uses_filter_plan_id_when_message_has_none():
    resolved = resolve_nearby_pharmacy_question(
        "what pharmacies are near zip 32801?",
        filter_plan_id=PLAN_FL_MAPD,
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "Icon Pharmacy" in explanation
    assert "Angels Pharmacy" not in explanation


def test_nearby_pharmacy_mail_order_wording_filters_to_mail_channel_only():
    resolved = resolve_nearby_pharmacy_question(
        f"which mail-order pharmacies are near zip 32801 for plan {PLAN_FL_PDP}?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "Accredo Health Group Inc" in explanation  # preferred_mail
    assert "Icon Pharmacy" not in explanation  # preferred_retail, filtered out
    assert "Angels Pharmacy" not in explanation  # standard_retail, filtered out


def test_nearby_pharmacy_retail_only_wording_not_confused_by_negated_mail_mention():
    """'retail only, not mail order' must resolve to a retail-channel filter, not match the
    literal 'mail order' substring inside the negation and invert the filter."""
    resolved = resolve_nearby_pharmacy_question(
        f"Which pharmacies near zip 32801 for plan {PLAN_FL_PDP} are retail-only, "
        "not mail order?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "Icon Pharmacy" in explanation  # preferred_retail
    assert "Angels Pharmacy" in explanation  # standard_retail
    assert "Accredo Health Group Inc" not in explanation  # preferred_mail, excluded


def test_nearby_pharmacy_mail_order_no_match_is_honest():
    """H8888-001's only networked pharmacy (Icon) is retail-only — a mail-order ask scoped
    to that plan must say so honestly, not silently drop the channel filter."""
    resolved = resolve_nearby_pharmacy_question(
        f"which mail-order pharmacies are near zip 32801 for plan {PLAN_FL_MAPD}?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "no mail-order pharmacies" in explanation.lower()
    assert "Icon Pharmacy" not in explanation


# --- Q4: resolve_plan_pharmacy_match_question (async) -----------------------------------
# Fixture facts (verified directly against the FL fixture, not assumed): metformin 500mg
# (rxcui 861007) is covered by every FL formulary (FORM0001/0002/0004/0005) — the 4
# non-suppressed FL plans are S9999-001 (PDP, FORM0001), H8888-001 (MAPD, FORM0002),
# H5427-060 (MOOP, FORM0002 — shares a formulary with H8888-001), S9999-004 (Partial
# Channels, FORM0005). Only S9999-001 and H8888-001 have a preferred-retail pharmacy
# (Icon Pharmacy) within 25 miles of ZIP 32801; H5427-060 and S9999-004 have zero pharmacy-
# network rows at all. Lovastatin 40mg (rxcui 197905) is on none of the FL formularies.


@pytest.mark.asyncio
async def test_plan_pharmacy_match_happy_path_lists_covered_plans_with_pharmacy():
    resolved = await resolve_plan_pharmacy_match_question(
        "I need to take metformin 500mg. I live in zip 32801. What pharmacies are near me?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "metformin 500mg" in explanation
    assert PLAN_FL_PDP in explanation and "Icon Pharmacy" in explanation
    assert PLAN_FL_MAPD in explanation
    assert tools == ["find_pharmacies"]
    assert artifacts["find_pharmacies"]["status"] == "ok"


@pytest.mark.asyncio
async def test_plan_pharmacy_match_discloses_covered_plans_without_nearby_pharmacy():
    resolved = await resolve_plan_pharmacy_match_question(
        "I need to take metformin 500mg. I live in zip 32801. What pharmacies are near me?"
    )
    assert resolved is not None
    explanation, *_ = resolved
    assert PLAN_FL_MAPD_MOOP in explanation
    assert PLAN_FL_PARTIAL_CHANNELS in explanation
    assert "didn't find" in explanation.lower() or "no preferred-retail" in explanation.lower()


@pytest.mark.asyncio
async def test_plan_pharmacy_match_no_plan_covers_drug_in_state():
    resolved = await resolve_plan_pharmacy_match_question(
        "I need to take lovastatin 40mg. I live in zip 32801. What pharmacies are near me?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "none" in explanation.lower()
    assert "lovastatin 40mg" in explanation
    assert tools == []


@pytest.mark.asyncio
async def test_plan_pharmacy_match_missing_dosage_asks_for_it():
    resolved = await resolve_plan_pharmacy_match_question(
        "I need to take metformin. I live in zip 32801. What pharmacies are near me?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "needs_clarification"
    assert "strength" in explanation.lower()


@pytest.mark.asyncio
async def test_plan_pharmacy_match_defers_when_plan_already_named():
    """Q1/Q2 own plan-scoped questions — Q4 must not double-answer."""
    resolved = await resolve_plan_pharmacy_match_question(
        f"I need to take metformin 500mg for plan {PLAN_FL_PDP}. I live in zip 32801. "
        "What pharmacies are near me?"
    )
    assert resolved is None


@pytest.mark.asyncio
async def test_plan_pharmacy_match_defers_plan_named_no_drug():
    resolved = await resolve_plan_pharmacy_match_question(
        f"what pharmacies are near zip 32801 for plan {PLAN_FL_MAPD}?"
    )
    assert resolved is None


@pytest.mark.asyncio
async def test_plan_pharmacy_match_defers_without_a_named_drug():
    resolved = await resolve_plan_pharmacy_match_question(
        "what pharmacies are near me? I live in zip 32801."
    )
    assert resolved is None


@pytest.mark.asyncio
async def test_plan_pharmacy_match_defers_when_state_has_no_fixture_plans():
    """ZIP 72719 resolves to a real state (AR) but the FL-only fixture has zero AR plans —
    Q4 must defer to Q3 rather than fabricate an answer. This is the literal bug-report
    message that motivated Q4."""
    resolved = await resolve_plan_pharmacy_match_question(
        "I need to take lovastatin 40mg and I live in 72719 what plans over this in "
        "near by pharamacies?"
    )
    assert resolved is None


def test_nearby_pharmacy_adds_unfiltered_caveat_when_q4_defers_and_drug_named():
    """Regression test for the original bug report: even when Q4 can't run (AR ZIP, no
    fixture plan data), Q3's fallback must acknowledge the drug and disclose it isn't
    coverage-filtered."""
    resolved = resolve_nearby_pharmacy_question(
        "I need to take lovastatin 40mg and I live in 72719 what plans over this in "
        "near by pharamacies?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "lovastatin" in explanation.lower()
    assert "not filtered" in explanation.lower()


# --- Q5: resolve_plan_coverage_question (async) ------------------------------------------
# Same FL fixture facts as Q4 above: metformin 500mg is covered by all 4 non-suppressed FL
# plans (S9999-001, H8888-001, H5427-060, S9999-004); lovastatin 40mg is on none of them.
# PLAN_FL_PDP metformin 500mg preferred_retail prices at $5.00 (see
# test_bug2_per_tier_deductible_exemption_overrides_phase in test_estimate_drug_cost.py).


def test_is_plan_coverage_question_matches_bug_transcript_without_pharmacy_word():
    """Root-cause regression: this exact user message (paraphrased from the bug report) says
    "plans" and "covers" but never "pharmacy" — it must match the Q5 trigger while missing
    every pharmacy-gated resolver, or it silently falls through to the LLM agent loop again."""
    message = (
        "I need to take lovastatin 40mg and I live in 72712 what plans are availalsblr "
        "that covers this medication in my zip?"
    )
    assert is_plan_coverage_question(message)
    assert not is_preferred_pharmacy_question(message)
    assert not is_nearby_pharmacy_question(message)


@pytest.mark.asyncio
async def test_plan_coverage_defers_when_pharmacy_wording_present():
    """Q1-Q4 keep first claim on anything mentioning pharmacy."""
    resolved = await resolve_plan_coverage_question(
        "I need to take metformin 500mg. I live in zip 32801. What plans cover this near a "
        "pharmacy?"
    )
    assert resolved is None


@pytest.mark.asyncio
async def test_plan_coverage_defers_when_plan_already_named():
    resolved = await resolve_plan_coverage_question(
        f"does plan {PLAN_FL_PDP} cover metformin 500mg? I live in zip 32801."
    )
    assert resolved is None


@pytest.mark.asyncio
async def test_plan_coverage_defers_without_zip():
    resolved = await resolve_plan_coverage_question("what plans cover metformin 500mg?")
    assert resolved is None


@pytest.mark.asyncio
async def test_plan_coverage_defers_when_state_has_no_fixture_plans():
    """ZIP 72712 resolves to a real state (AR) but the FL-only fixture has zero AR plans —
    Q5 must defer rather than fabricate an answer. This is the literal bug-report message."""
    resolved = await resolve_plan_coverage_question(
        "I need to take lovastatin 40mg and I live in 72712 what plans are availalsblr "
        "that covers this medication in my zip?"
    )
    assert resolved is None


@pytest.mark.asyncio
async def test_plan_coverage_no_plan_covers_drug():
    resolved = await resolve_plan_coverage_question(
        "what plans cover lovastatin 40mg? I live in zip 32801."
    )
    assert resolved is not None
    explanation, tool_artifacts, tools, status = resolved
    assert status == "ok"
    assert "none" in explanation.lower()
    assert "lovastatin 40mg" in explanation
    assert tools == []
    assert tool_artifacts == {}


@pytest.mark.asyncio
async def test_plan_coverage_happy_path_lists_priced_plan_cheapest_first():
    resolved = await resolve_plan_coverage_question(
        "what plans cover metformin 500mg? I live in zip 32801."
    )
    assert resolved is not None
    explanation, tool_artifacts, tools, status = resolved
    assert status == "ok"
    assert "metformin 500mg" in explanation
    assert PLAN_FL_PDP in explanation
    assert "$5.00" in explanation
    assert tools == ["estimate_drug_cost_all_channels"]
    assert tool_artifacts["estimate_drug_cost_all_channels"]["status"] == "ok"
    calls = tool_artifacts["estimate_drug_cost_all_channels__calls"]
    assert 1 <= len(calls) <= 5
    for artifact in calls:
        assert artifact["status"] == "ok"


@pytest.mark.asyncio
async def test_plan_coverage_missing_dosage_asks_for_it():
    resolved = await resolve_plan_coverage_question(
        "what plans cover metformin? I live in zip 32801."
    )
    assert resolved is not None
    explanation, tool_artifacts, tools, status = resolved
    assert status == "needs_clarification"
    assert "strength" in explanation.lower()


# --- Q2: resolve_pharmacy_cost_question (async) -----------------------------------------


@pytest.mark.asyncio
async def test_pharmacy_cost_defers_without_named_drug():
    resolved = await resolve_pharmacy_cost_question(
        f"what are my preferred pharmacies for zip 32801 and plan {PLAN_FL_PDP}?"
    )
    assert resolved is None


@pytest.mark.asyncio
async def test_pharmacy_cost_missing_zip_asks_for_it():
    resolved = await resolve_pharmacy_cost_question(
        f"how much does metformin 500mg cost at my preferred pharmacy for plan {PLAN_FL_PDP}?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "needs_clarification"
    assert "zip" in explanation.lower()


@pytest.mark.asyncio
async def test_pharmacy_cost_missing_plan_asks_for_it():
    resolved = await resolve_pharmacy_cost_question(
        "how much does metformin 500mg cost at my preferred pharmacy in zip 32801?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "needs_clarification"
    assert "plan" in explanation.lower()


@pytest.mark.asyncio
async def test_pharmacy_cost_missing_dosage_asks_for_it():
    resolved = await resolve_pharmacy_cost_question(
        f"how much does metformin cost at my preferred pharmacy in zip 32801 for plan {PLAN_FL_PDP}?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "needs_clarification"
    assert "strength" in explanation.lower()


@pytest.mark.asyncio
async def test_pharmacy_cost_ok_names_pharmacy_and_price():
    resolved = await resolve_pharmacy_cost_question(
        "how much does metformin 500mg cost at my preferred pharmacy in zip 32801 "
        f"for plan {PLAN_FL_PDP}?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "Icon Pharmacy" in explanation
    assert "preferred-retail" in explanation.lower() or "preferred retail" in explanation.lower()
    assert "$" in explanation
    assert tools == ["find_pharmacies", "estimate_drug_cost_all_channels"]
    assert artifacts["find_pharmacies"]["status"] == "ok"
    assert artifacts["estimate_drug_cost_all_channels"]["status"] == "ok"
    assert "estimate_drug_cost_all_channels__calls" in artifacts


@pytest.mark.asyncio
async def test_pharmacy_cost_insulin_product_names_pharmacy_and_price():
    """Insulin products are excluded from mentioned_oral_drugs_with_strength (that helper's
    job is oral-strength parsing; insulin_requests.py owns insulin pricing) — without also
    pulling insulin products into _extract_drug_dosage_pairs, this resolver sees no drug,
    defers, and Q1 (resolve_preferred_pharmacy_question, which doesn't gate on a drug at all)
    answers with a bare pharmacy list that silently drops the cost question."""
    resolved = await resolve_pharmacy_cost_question(
        f"how much does Lantus cost at my preferred pharmacy in zip 32801 for plan {PLAN_FL_PDP}?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "Icon Pharmacy" in explanation
    assert "lantus" in explanation.lower()
    assert "$" in explanation
    assert tools == ["find_pharmacies", "estimate_drug_cost_all_channels"]


@pytest.mark.asyncio
async def test_pharmacy_cost_no_preferred_pharmacy_found_is_honest():
    resolved = await resolve_pharmacy_cost_question(
        "how much does metformin 500mg cost at my preferred pharmacy in zip 90001 "
        f"for plan {PLAN_FL_PDP}?"
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "no pharmacies" in explanation.lower()
    assert tools == ["find_pharmacies"]


def test_build_find_pharmacies_session_call_from_live_in_phrasing():
    call = build_find_pharmacies_session_call("I live in 72719. Any pharmacies near me?")
    assert call == {"name": "find_pharmacies", "arguments": {"zip_code": "72719"}}


def test_resolve_pharmacy_radius_follow_up_is_honest_after_results():
    last_calls = [
        build_find_pharmacies_session_call(
            "I live in zip 72719. Any pharmacies near me?",
            had_results=True,
        )
    ]
    resolved = resolve_pharmacy_radius_follow_up(
        "can you check within 50 miles instead?", last_calls
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "can't widen" in explanation.lower() or "cannot widen" in explanation.lower()
    assert "50 miles" not in explanation.lower()
    assert "no pharmacies" not in explanation.lower()
    assert "previous reply" in explanation.lower()
    assert tools == []


def test_resolve_pharmacy_radius_follow_up_is_honest_after_no_match():
    last_calls = [
        build_find_pharmacies_session_call(
            "I live in zip 72719. Any pharmacies near me?",
            had_results=False,
        )
    ]
    resolved = resolve_pharmacy_radius_follow_up(
        "can you check within 50 miles instead?", last_calls
    )
    assert resolved is not None
    explanation, _artifacts, tools, status = resolved
    assert status == "ok"
    assert "no pharmacies" in explanation.lower()
    assert tools == []


def test_resolve_pharmacy_radius_follow_up_is_honest():
    last_calls = [
        build_find_pharmacies_session_call("I live in zip 72719. Any pharmacies near me?")
    ]
    resolved = resolve_pharmacy_radius_follow_up(
        "can you check within 50 miles instead?", last_calls
    )
    assert resolved is not None
    explanation, artifacts, tools, status = resolved
    assert status == "ok"
    assert "can't widen" in explanation.lower() or "cannot widen" in explanation.lower()
    assert "50 miles" not in explanation.lower()
    assert "no pharmacies" not in explanation.lower()
    assert tools == []


def test_resolve_pharmacy_radius_follow_up_defers_without_prior_pharmacy_call():
    assert resolve_pharmacy_radius_follow_up("check within 50 miles instead?", []) is None


def test_format_pharmacy_distance_suffix_omits_zero_miles():
    assert _format_pharmacy_distance_suffix(0.0) is None
    assert _format_pharmacy_distance_suffix(0) is None
    assert _format_pharmacy_distance_suffix(2.6) == "2.6 mi away"


def test_pharmacy_list_sentence_omits_zero_mile_distance():
    pharmacies = [
        PharmacyResult(
            npi="1841304730",
            pharmacy_name="Icon Pharmacy",
            address_line1="300 E Church St",
            city="Orlando",
            state="FL",
            zip_code="32801",
            distance_miles=0.0,
        )
    ]
    sentence = _pharmacy_list_sentence(pharmacies)
    assert "0.0 mi away" not in sentence
    assert "0 mi away" not in sentence
    assert "Icon Pharmacy" in sentence


def test_pharmacy_results_header_states_search_radius():
    header = _pharmacy_results_header(zip_code="72712", label="Pharmacies")
    assert header == "Pharmacies within 25 miles of ZIP 72712:"


def test_nearby_pharmacy_ok_header_includes_radius():
    resolved = resolve_nearby_pharmacy_question(
        "what pharmacies are near me? I live in zip 32801."
    )
    assert resolved is not None
    explanation, _, _, status = resolved
    assert status == "ok"
    assert explanation.startswith("Pharmacies within 25 miles of ZIP 32801:")
    assert "0.0 mi away" not in explanation
