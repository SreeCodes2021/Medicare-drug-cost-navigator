import pytest

from medicare_navigator.config import settings
from medicare_navigator.orchestrator.pipeline import orchestrator as legacy_orchestrator
from tests.spuf_fixture import PLAN_FL_MAPD, PLAN_FL_PDP, load_spuf_fixture


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


@pytest.fixture(autouse=True)
def legacy_pipeline(monkeypatch):
    monkeypatch.setattr(settings, "navigator_mode", "legacy_pipeline")


@pytest.mark.asyncio
async def test_pipeline_runs_policy_for_explain_cost_change_intent():
    response = await legacy_orchestrator.run(
        f"why did lisinopril cost go up on plan {PLAN_FL_PDP}"
    )
    assert response.status == "ok"
    assert "policy" in response.agents_invoked
    assert "policy_retrieval" in response.tools_invoked


@pytest.mark.asyncio
async def test_pipeline_runs_policy_for_deductible_keyword():
    response = await legacy_orchestrator.run(
        f"explain deductible phase for lisinopril on plan {PLAN_FL_PDP}"
    )
    assert response.status == "ok"
    assert "policy" in response.agents_invoked


@pytest.mark.asyncio
async def test_pipeline_runs_policy_for_catastrophic_keyword():
    response = await legacy_orchestrator.run(
        f"what happens in catastrophic coverage for metformin plan {PLAN_FL_MAPD}"
    )
    assert response.status == "ok"
    assert "policy" in response.agents_invoked


@pytest.mark.asyncio
async def test_pipeline_skips_policy_for_plain_tier_lookup():
    response = await legacy_orchestrator.run(
        f"lisinopril tier copay on plan {PLAN_FL_PDP}"
    )
    assert response.status == "ok"
    assert "policy" not in response.agents_invoked
    assert "policy_retrieval" not in response.tools_invoked


@pytest.mark.asyncio
async def test_pipeline_skips_policy_on_reuse_artifacts():
    session_id = None
    r1 = await legacy_orchestrator.run(
        f"show alternatives to metformin on plan {PLAN_FL_MAPD}",
        session_id=session_id,
    )
    session_id = r1.session_id
    r2 = await legacy_orchestrator.run(
        "how many alternatives did you find?",
        session_id=session_id,
    )
    assert r2.status == "ok"
    assert "policy" not in r2.agents_invoked


@pytest.mark.asyncio
async def test_pipeline_policy_no_match_surfaces_honestly(tmp_path, monkeypatch):
    data_dir = tmp_path / "nopolicy"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "duckdb_path", data_dir / "navigator.duckdb")
    monkeypatch.setattr(settings, "chroma_path", data_dir / "chroma")
    load_spuf_fixture(data_dir=data_dir, seed_policy=False)

    response = await legacy_orchestrator.run(
        f"why did lisinopril cost go up on plan {PLAN_FL_PDP}"
    )
    assert response.status == "ok"
    assert response.tool_statuses.get("policy_retrieval") == "no_match"


@pytest.mark.asyncio
async def test_pipeline_synthesis_includes_policy_claims_in_response():
    response = await legacy_orchestrator.run(
        f"why did lisinopril cost go up on plan {PLAN_FL_PDP}"
    )
    assert response.status == "ok"
    has_policy_citation = any(
        c.source_id == "cms_policy_corpus" for c in response.citations
    )
    has_policy_text = any(
        kw in response.explanation.lower()
        for kw in ("benefit phase", "deductible", "part d")
    )
    assert has_policy_citation or has_policy_text
