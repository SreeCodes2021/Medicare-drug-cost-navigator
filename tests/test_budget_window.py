"""Phase 3: budget_start_date threading through the estimate tool and, end to end, through
the mediator's explicit-date extraction into the deterministic insulin remaining-year path.
"""

from __future__ import annotations

from datetime import date

import pytest

from medicare_navigator.agent.navigator import navigator
from medicare_navigator.config import settings
from medicare_navigator.tools.estimate_drug_cost import estimate_drug_cost_all_channels
from tests.spuf_fixture import PLAN_FL_PDP


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


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
async def test_mediator_extracted_start_date_flows_into_deterministic_insulin_response(
    monkeypatch,
):
    """End to end: 'starting September 1' phrasing, mediator enabled, resolves through the
    deterministic insulin path (not the general agent loop) using the explicit start date —
    not silently substituting today's date."""
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
