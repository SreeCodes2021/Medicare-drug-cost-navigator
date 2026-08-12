"""Load-bearing regression suite for the mediator-first design: since every message now
passes through the mediator when MEDIATOR_ENABLED=1, this is what would catch the mediator
corrupting an already-good message, not just verifying new mediator-specific behavior.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from medicare_navigator.agent import mediator as mediator_module
from medicare_navigator.agent.navigator import navigator
from medicare_navigator.config import settings
from medicare_navigator.session.manager import session_manager
from tests.spuf_fixture import PLAN_FL_MAPD, PLAN_FL_PDP


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


@pytest.fixture
def mediator_on(monkeypatch):
    monkeypatch.setattr(settings, "mediator_enabled", True)


@pytest.mark.asyncio
async def test_mediator_on_does_not_change_response_source_for_clean_messages(monkeypatch):
    """The primary regression gate: mediator-normalized text must resolve to the same
    deterministic route as the raw message did before the mediator existed."""
    plan = PLAN_FL_PDP
    cases = [
        f"What is Lantus 30day supply for on plan {plan}?",
        f"metformin 500mg cost on plan {plan}, spent $0 this year",
        f"What formulary tier is Lantus on plan {plan}?",
        "Is insulin always exactly $35, or can it be lower?",
        "Should I take metformin with food?",
        "How do I enroll in Medicare Part D?",
    ]
    for message in cases:
        # Fresh session per call (no session_id) so pending_clarification/last_tool_calls
        # from one run never leak into the other.
        monkeypatch.setattr(settings, "mediator_enabled", False)
        baseline = await navigator.run(message)
        monkeypatch.setattr(settings, "mediator_enabled", True)
        mediated_response = await navigator.run(message)
        assert baseline.response_source == mediated_response.response_source, message


@pytest.mark.asyncio
async def test_mediator_on_insulin_still_resolves_deterministically(mediator_on):
    response = await navigator.run(f"Lantus on plan {PLAN_FL_MAPD} for 90 days")
    assert response.response_source == "System/Insulin"
    assert response.mediator_llm_usage is not None
    assert response.mediator_llm_usage.total_tokens > 0
    assert response.total_llm_usage is not None
    assert response.total_llm_usage.total_tokens == response.mediator_llm_usage.total_tokens


@pytest.mark.asyncio
async def test_mediator_disabled_by_default_no_usage_recorded():
    response = await navigator.run(f"Lantus on plan {PLAN_FL_MAPD} for 90 days")
    assert response.response_source == "System/Insulin"
    assert response.mediator_llm_usage is None
    assert response.total_llm_usage is None


@pytest.mark.asyncio
async def test_mediator_failure_falls_back_to_identical_raw_behavior(mediator_on, monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated mediator outage")

    monkeypatch.setattr(mediator_module, "rewrite_and_extract", _boom)

    message = f"Lantus on plan {PLAN_FL_MAPD} for 90 days"
    with_mediator_down = await navigator.run(message)
    monkeypatch.setattr(settings, "mediator_enabled", False)
    without_mediator = await navigator.run(message)

    assert with_mediator_down.response_source == without_mediator.response_source
    assert with_mediator_down.mediator_llm_usage is None


@pytest.mark.asyncio
async def test_pending_clarification_round_trip(mediator_on):
    session_id = "test-pending-clarification-session"
    first = await navigator.run("metformin cost", session_id=session_id)
    assert first.status == "needs_clarification"

    session = session_manager.get_or_create(session_id)
    pending = session.get("pending_clarification")
    assert pending is not None
    assert pending["drugs"] == ["metformin"]

    # A genuinely bare reply — the splice is deliberately narrow (see run()): it only
    # fires for a bare strength, not "500mg on plan X". No plan is discoverable anywhere
    # in text here, so the mock loop will reasonably still ask for one — the guarantee
    # under test is that "metformin" was recalled, not re-asked-for.
    second = await navigator.run("500mg", session_id=session_id)
    assert "which drug" not in second.explanation.lower()
    assert "metformin" in second.explanation.lower() or second.drug_name == "metformin"

    session_after = session_manager.get_or_create(session_id)
    assert session_after.get("pending_clarification") is None


@pytest.mark.asyncio
async def test_casual_typo_message_never_fabricates_a_real_drug(mediator_on):
    """The mediator-level guarantee (see test_mediator.py) is that it never turns an
    unrecognizable typo into a specific drug. This checks that guarantee survives end to
    end through the navigator, not just in the mediator's own output — the general agent
    loop's mock uses its own crude token-guessing heuristic once nothing resolves
    deterministically, so this deliberately doesn't assert on its exact wording, only that
    no real drug name ever appears in place of "durg"."""
    response = await navigator.run(
        "Hey!ssup. need 30 days supply of durg for mom. how much?"
    )
    lower = response.explanation.lower()
    assert "durg" not in lower
    assert "metformin" not in lower and "lantus" not in lower
