import pytest

from medicare_navigator.agent.pharmacy_questions import (
    build_find_pharmacies_session_call,
    extract_zip,
    is_nearby_pharmacy_question,
    is_preferred_pharmacy_question,
    resolve_nearby_pharmacy_question,
    resolve_pharmacy_cost_question,
    resolve_pharmacy_radius_follow_up,
    resolve_preferred_pharmacy_question,
)
from tests.spuf_fixture import PLAN_FL_MAPD, PLAN_FL_PDP


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
    assert tools == []


def test_resolve_pharmacy_radius_follow_up_defers_without_prior_pharmacy_call():
    assert resolve_pharmacy_radius_follow_up("check within 50 miles instead?", []) is None
