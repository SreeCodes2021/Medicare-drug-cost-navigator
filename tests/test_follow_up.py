import pytest

from medicare_navigator.ingestion.seed import run_seed
from medicare_navigator.intake.merger import InputMerger
from medicare_navigator.models.query import QuerySlots
from medicare_navigator.orchestrator.pipeline import orchestrator


@pytest.fixture(scope="module", autouse=True)
def seed_data():
    run_seed()


@pytest.mark.asyncio
async def test_lipitor_alternatives_follow_up_count():
    session_id = None
    r1 = await orchestrator.run("show alternatives to lipitor", session_id=session_id)
    session_id = r1.session_id
    assert r1.status == "ok"
    assert r1.alternatives

    r2 = await orchestrator.run("Did you find only one alternative?", session_id=session_id)
    assert r2.status == "ok"
    assert r2.explanation != r1.explanation
    lower = r2.explanation.lower()
    assert "yes" in lower or "one" in lower or "1" in lower
    assert "atorvastatin" in lower


@pytest.mark.asyncio
async def test_ytd_spend_carries_over_on_follow_up():
    session_id = None
    r1 = await orchestrator.run(
        "atorvastatin 20mg tier H1234-001 spent $400 YTD",
        session_id=session_id,
    )
    session_id = r1.session_id
    assert r1.status == "ok"
    assert r1.formulary is not None
    assert r1.formulary.benefit_phase == "deductible"
    assert r1.formulary.ytd_oop_spend == 400.0

    r2 = await orchestrator.run("what is the tier?", session_id=session_id)
    assert r2.status == "ok"
    assert r2.formulary is not None
    assert r2.formulary.ytd_oop_spend == 400.0
    assert r2.formulary.benefit_phase == "deductible"


@pytest.mark.asyncio
async def test_ytd_spend_updates_on_follow_up():
    session_id = None
    r1 = await orchestrator.run(
        "atorvastatin 20mg tier H1234-001 spent $400 YTD",
        session_id=session_id,
    )
    session_id = r1.session_id

    r2 = await orchestrator.run("what if I've spent $800?", session_id=session_id)
    assert r2.status == "ok"
    assert r2.formulary is not None
    assert r2.formulary.ytd_oop_spend == 800.0
    assert r2.formulary.benefit_phase == "initial_coverage"


@pytest.mark.asyncio
async def test_tier_lookup_includes_alternatives_and_drug_fields():
    r = await orchestrator.run("lipitor 40mg tier and copay on plan H1234-045")
    assert r.status == "ok"
    assert r.drug_name
    assert r.rxcui
    assert "alternatives_finder" in r.tools_invoked
    assert r.alternatives


@pytest.mark.asyncio
async def test_not_covered_includes_formulary_in_response():
    r = await orchestrator.run("januvia 100mg plan S5678-018")
    assert r.status == "ok"
    assert r.formulary is not None
    assert r.formulary.covered is False
    assert r.formulary.benefit_phase is None
    assert r.tool_statuses.get("formulary_benefit_lookup") == "not_covered"


def test_merger_ignores_zero_ytd_filter():
    filters = QuerySlots(plan_id="H1234-045", ytd_oop_spend=0.0)
    chat = QuerySlots(drug="metformin", raw_message="metformin tier")
    merged = InputMerger.merge(chat, filter_slots=filters, raw_message="metformin tier")
    assert merged.ytd_oop_spend is None


def test_merger_preserves_ytd_when_not_mentioned():
    session = QuerySlots(drug="metformin", ytd_oop_spend=400.0, intents=["tier_lookup"])
    chat = QuerySlots(ytd_oop_spend=0.0, raw_message="what is the tier?")
    merged = InputMerger.merge(chat, session_slots=session, raw_message="what is the tier?")
    assert merged.ytd_oop_spend == 400.0


def test_merger_unions_intents_on_follow_up():
    session = QuerySlots(drug="lipitor", intents=["alternatives"])
    chat = QuerySlots(intents=["tier_lookup"], raw_message="Did you find only one alternative?")
    merged = InputMerger.merge(
        chat,
        session_slots=session,
        raw_message="Did you find only one alternative?",
    )
    assert "alternatives" in merged.intents
