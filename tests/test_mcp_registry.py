import pytest

from medicare_navigator.config import settings
from medicare_navigator.ingestion.schema import ensure_schema
from medicare_navigator.mcp.registry import call_tool, tool_names
from medicare_navigator.mcp.schemas import openai_tools
from medicare_navigator.storage.connection import DuckDBConnection
from tests.spuf_fixture import NDC_METFORMIN, PLAN_FL_MAPD, PLAN_FL_PDP


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


def test_tool_names():
    names = tool_names()
    assert "normalize_drug" in names
    assert "formulary_benefit_lookup" in names
    assert "lookup_plan" in names
    assert len(names) == 7


def test_openai_tool_schemas():
    tools = openai_tools()
    assert all(t["type"] == "function" for t in tools)
    assert tools[0]["function"]["name"] == "normalize_drug"


@pytest.mark.asyncio
async def test_mcp_normalize_drug():
    result = await call_tool("normalize_drug", {"drug_name": "lisinopril", "dosage": "10mg"})
    assert result["status"] == "ok"
    assert result["data"]["selected"]["rxcui"] == "29046"


@pytest.mark.asyncio
async def test_mcp_lookup_plan_exact():
    result = await call_tool("lookup_plan", {"plan_key": PLAN_FL_PDP})
    assert result["status"] == "ok"
    assert result["data"]["plan"]["plan_key"] == PLAN_FL_PDP


@pytest.mark.asyncio
async def test_mcp_formulary_matches_direct():
    direct = await call_tool(
        "formulary_benefit_lookup",
        {"plan_key": PLAN_FL_MAPD, "ndc": NDC_METFORMIN, "ytd_oop_spend": 0},
    )
    assert direct["status"] == "ok"
    assert direct["data"]["tier"] == 2


@pytest.mark.asyncio
async def test_mcp_policy_retrieval_ok_after_seed():
    result = await call_tool("policy_retrieval", {"query_text": "deductible"})
    assert result["status"] == "ok"
    assert result["source_id"] == "cms_policy_corpus"
    assert result["data"]


@pytest.mark.asyncio
async def test_mcp_policy_retrieval_no_match_empty(tmp_path, monkeypatch):
    data_dir = tmp_path / "empty"
    data_dir.mkdir()
    duckdb_path = data_dir / "empty.duckdb"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "duckdb_path", duckdb_path)
    monkeypatch.setattr(settings, "chroma_path", data_dir / "chroma")
    ensure_schema(DuckDBConnection(path=duckdb_path))
    result = await call_tool("policy_retrieval", {"query_text": "deductible"})
    assert result["status"] == "no_match"


@pytest.mark.asyncio
async def test_mcp_policy_retrieval_query_text_required():
    result = await call_tool("policy_retrieval", {})
    assert result["status"] in ("no_match", "ok")
