"""Phase 3: budget_start_date threading through the estimate tool and, end to end, through
the mediator's explicit-date extraction into the deterministic insulin remaining-year path.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from medicare_navigator.agent.navigator import navigator
from medicare_navigator.config import settings
from medicare_navigator.tools.estimate_drug_cost import estimate_drug_cost_all_channels
from tests.spuf_fixture import PLAN_FL_PDP


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


def _freeze_datetime(monkeypatch, fixed: datetime) -> None:
    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr("medicare_navigator.agent.datetime_context.datetime", _FixedDatetime)


@pytest.mark.asyncio
async def test_budget_start_date_narrows_the_remaining_year_window():
    today_result = await estimate_drug_cost_all_channels(
        plan_key=PLAN_FL_PDP, drug_name="lantus", days_supply=30, ytd_oop_spend=0.0
    )
    later_result = await estimate_drug_cost_all_channels(
        plan_key=PLAN_FL_PDP,
        drug_name="lantus",
        days_supply=30,
        ytd_oop_spend=0.0,
        budget_start_date=date(2026, 12, 1),
    )
    assert today_result.data.remaining_year_days > later_result.data.remaining_year_days
    assert today_result.data.remaining_year_fills >= later_result.data.remaining_year_fills
    # Dec 1 -> Dec 31 is 30 days, one 30-day fill.
    assert later_result.data.remaining_year_days == 30
    assert later_result.data.remaining_year_fills == 1


@pytest.mark.asyncio
async def test_budget_start_date_none_is_unchanged_from_today():
    from medicare_navigator.agent.datetime_context import window_days_remaining
    from medicare_navigator.agent.request_context import set_request_timezone

    set_request_timezone(None)
    with_none = await estimate_drug_cost_all_channels(
        plan_key=PLAN_FL_PDP,
        drug_name="lantus",
        days_supply=30,
        ytd_oop_spend=0.0,
        budget_start_date=None,
    )
    assert with_none.data.remaining_year_days == window_days_remaining(2026, None)


@pytest.mark.asyncio
async def test_remaining_year_no_explicit_start_uses_deterministic_insulin_path():
    """End to end equivalent of /quality-test §2h scenario #1: "rest of the year" phrasing
    with no explicit start date at all. This is detected by the regex-based
    INSULIN_INTENT_REMAINING_YEAR path (agent/insulin_requests.py), not the mediator, so it
    runs with the mediator disabled — the default in production (MEDIATOR_ENABLED=False).
    Unlike scenario #2 (test_mediator_extracted_start_date_flows_into_deterministic_insulin_response
    below), nothing before this test exercised this phrasing through navigator.run() and
    checked response_source/explanation; test_insulin.py only asserts intent detection and
    sentence formatting in isolation."""
    from medicare_navigator.agent.datetime_context import window_days_remaining
    from medicare_navigator.agent.request_context import set_request_timezone

    set_request_timezone(None)
    response = await navigator.run(
        f"What will Lantus cost me for the rest of the year on plan {PLAN_FL_PDP}?"
    )
    assert response.response_source == "System/Insulin"
    assert "remaining" in response.explanation.lower()
    assert "fill" in response.explanation.lower()
    today_result = await estimate_drug_cost_all_channels(
        plan_key=PLAN_FL_PDP, drug_name="lantus", days_supply=30, ytd_oop_spend=0.0
    )
    assert today_result.data.remaining_year_days == window_days_remaining(2026, None)


@pytest.mark.asyncio
async def test_mediator_extracted_start_date_flows_into_deterministic_insulin_response(
    monkeypatch,
):
    """End to end: 'starting September 1' phrasing, mediator enabled, resolves through the
    deterministic insulin path (not the general agent loop) using the explicit start date —
    not silently substituting today's date.

    Time is frozen to 2026-08-03 so September 1 is still in the future; after Sep 1 passes,
    resolve_explicit_start_date rolls to next year and the remaining-year window zeros out.
    Live complement: /quality-test §2h scenario #2 (use a future month/day each run).
    """
    _freeze_datetime(
        monkeypatch,
        datetime(2026, 8, 3, 12, 0, tzinfo=ZoneInfo("America/Chicago")),
    )
    monkeypatch.setattr(settings, "mediator_enabled", True)

    response = await navigator.run(
        f"Lantus on plan {PLAN_FL_PDP} for the rest of the year starting September 1"
    )
    assert response.response_source == "System/Insulin"
    assert response.mediator_llm_usage is not None
    # Sanity: the rendered sentence should describe a multi-fill remaining-year total, not
    # the single 30-day-fill sentence the same drug/plan would get without this intent.
    assert "remaining" in response.explanation.lower()
    assert "fill" in response.explanation.lower()


@pytest.mark.asyncio
async def test_mixed_basket_with_duration_never_takes_the_duration_blind_deterministic_path(
    monkeypatch,
):
    """Regression for the exact bug this design was built to fix: MixedBasketRequest has no
    duration/date field at all, so resolve_mixed_basket_request would previously match, silently
    ignore "the next 3 months," and return a confidently-wrong single-fill total. With the
    mediator enabled and a date/duration signal detected, this must fall through to the agent
    loop (Phase 3b) instead of System/MixedBasket."""
    monkeypatch.setattr(settings, "mediator_enabled", True)

    response = await navigator.run(
        "budget Lantus and metformin 500mg for the next 3 months starting Feb 13 "
        f"on plan {PLAN_FL_PDP}"
    )
    assert response.response_source != "System/MixedBasket"
    assert response.mediator_llm_usage is not None


@pytest.mark.asyncio
async def test_mixed_basket_without_duration_still_uses_deterministic_path(monkeypatch):
    """Confirms the fix above is scoped to date/duration signals only — an ordinary mixed
    basket ask with no date modifier must still resolve deterministically."""
    monkeypatch.setattr(settings, "mediator_enabled", True)

    response = await navigator.run(
        f"Lantus and metformin 500mg on plan {PLAN_FL_PDP}"
    )
    assert response.response_source == "System/MixedBasket"


@pytest.mark.asyncio
async def test_mixed_basket_fuzzy_duration_without_digit_still_reaches_mixed_basket(
    monkeypatch,
):
    """Documents a real gap in the without-mediator guard: _DURATION_PHRASE_RE (the regex
    fallback used when MEDIATOR_ENABLED=False, the production default) only matches
    duration phrases with a leading digit ("next 3 months"). Natural phrasing without a
    number — "the next few months", "a couple months", bare "next month" — does not match,
    so has_unhandled_date_window is False and this falls straight into the duration-blind
    System/MixedBasket single-fill path: the exact bug class this design was built to
    prevent, just for un-numbered duration wording instead of numbered. Locked in as
    documentation of current behavior, not a desired outcome — if _DURATION_PHRASE_RE grows
    fuzzy-quantifier support, this test should start failing and needs updating."""
    monkeypatch.setattr(settings, "mediator_enabled", False)

    response = await navigator.run(
        f"Budget lantus and metformin 500mg for the next few months on plan {PLAN_FL_PDP}"
    )
    assert response.response_source == "System/MixedBasket"


@pytest.mark.asyncio
async def test_mixed_basket_with_duration_avoids_deterministic_path_without_mediator(
    monkeypatch,
):
    """Regression for the T3 2026-08-12 §2h-3 BLOCK: MEDIATOR_ENABLED defaults to False, so
    `date_context` is always None in production unless the operator opts in. The duration
    guard must not rely solely on the mediator being on — a cheap regex fallback
    (_DURATION_PHRASE_RE) should catch "the next 3 months" even with the mediator disabled,
    so this never falls into the duration-blind System/MixedBasket path and garbles "months"
    into a fake drug name."""
    monkeypatch.setattr(settings, "mediator_enabled", False)

    response = await navigator.run(
        f"Budget lantus and metformin 500mg for the next 3 months on plan {PLAN_FL_PDP}"
    )
    assert response.response_source != "System/MixedBasket"
    assert "months" not in response.explanation.lower()


@pytest.mark.asyncio
async def test_pharmacy_cost_with_duration_avoids_the_single_fill_deterministic_path():
    """Regression (compound-questions suite CC4/CC5): resolve_pharmacy_cost_question (Q2)
    prices a single fixed days_supply fill with no duration/date-window field at all — the
    exact same "silently single-fill a multi-month ask" bug MixedBasketRequest was fixed
    for above, just reached through "preferred pharmacy" wording instead of a plain
    multi-drug basket. Before the fix this returned "$35.00 for a 30-day supply" as if it
    answered "for the rest of the year." Must not take the deterministic System/PharmacyCost
    path when a duration signal is present."""
    response = await navigator.run(
        f"What's the cost of lantus at my nearest preferred pharmacy in zip 32801 on plan "
        f"{PLAN_FL_PDP}, for the rest of the year?"
    )
    assert response.response_source != "System/PharmacyCost"


@pytest.mark.asyncio
async def test_preferred_pharmacy_list_with_duration_and_drug_also_avoids_bare_list():
    """Regression (compound-questions suite CC6): once Q2 defers on a duration signal, Q1
    (resolve_preferred_pharmacy_question) is the next resolver whose "preferred pharmac..."
    pattern still matches the same message — before this fix it answered with a bare
    pharmacy list and silently dropped the multi-month cost question a second time, just
    through a different resolver. Only suppressed when a priceable drug is also named;
    see the control test below for the no-drug case."""
    response = await navigator.run(
        f"I take lantus and metformin 500mg on plan {PLAN_FL_PDP} - what will my costs be "
        f"for the next 3 months, and are there preferred pharmacies near zip 32801 that "
        f"carry both?"
    )
    assert response.response_source != "System/PreferredPharmacy"


@pytest.mark.asyncio
async def test_preferred_pharmacy_list_with_duration_but_no_drug_still_uses_fast_path():
    """Control for the guard above: a duration phrase with no drug named at all isn't a
    cost question Q1 could get wrong — must still answer immediately via the deterministic
    System/PreferredPharmacy path, not be needlessly deferred to the agent loop."""
    response = await navigator.run(
        f"What are my preferred pharmacies near zip 32801 on plan {PLAN_FL_PDP} for the "
        f"next 3 months?"
    )
    assert response.response_source == "System/PreferredPharmacy"
