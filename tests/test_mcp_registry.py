import pytest

from medicare_navigator.ingestion.seed import run_seed
from medicare_navigator.mcp.registry import call_tool, tool_names
from medicare_navigator.mcp.schemas import openai_tools


@pytest.fixture(scope="module", autouse=True)
def seed_data():
    run_seed()


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
    result = await call_tool("lookup_plan", {"plan_key": "S5678-012"})
    assert result["status"] == "ok"
    assert result["data"]["plan"]["plan_key"] == "S5678-012"


@pytest.mark.asyncio
async def test_mcp_formulary_matches_direct():
    direct = await call_tool(
        "formulary_benefit_lookup",
        {"plan_key": "H1234-045", "ndc": "00093-7214-01", "ytd_oop_spend": 0},
    )
    assert direct["status"] == "ok"
    assert direct["data"]["tier"] == 1
