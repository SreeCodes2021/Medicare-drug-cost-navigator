import pytest

from medicare_navigator.mcp.registry import call_tool, tool_names
from medicare_navigator.mcp.schemas import openai_tools
from tests.spuf_fixture import PLAN_FL_MAPD, PLAN_FL_PDP, PLAN_FL_SUPPRESSED


@pytest.fixture(autouse=True)
def _spuf(spuf_db):
    pass


def test_tool_names():
    names = tool_names()
    assert "estimate_drug_cost" in names
    assert "lookup_plan" in names
    assert "list_plans" in names
    assert len(names) == 3


def test_openai_tool_schemas():
    tools = openai_tools()
    assert all(t["type"] == "function" for t in tools)
    assert tools[0]["function"]["name"] == "estimate_drug_cost"


@pytest.mark.asyncio
async def test_mcp_lookup_plan_exact():
    result = await call_tool("lookup_plan", {"plan_key": PLAN_FL_PDP})
    assert result["status"] == "ok"
    assert result["data"]["plan"]["plan_key"] == PLAN_FL_PDP


@pytest.mark.asyncio
async def test_mcp_estimate_drug_cost_ok():
    result = await call_tool(
        "estimate_drug_cost",
        {"plan_key": PLAN_FL_MAPD, "drug_name": "metformin", "ytd_oop_spend": 0},
    )
    assert result["status"] == "ok"
    assert result["data"]["tiers_matched"] == [2]
    assert result["data"]["cost_low"] is not None


@pytest.mark.asyncio
async def test_mcp_estimate_drug_cost_suppressed_plan():
    result = await call_tool(
        "estimate_drug_cost",
        {"plan_key": PLAN_FL_SUPPRESSED, "drug_name": "metformin"},
    )
    assert result["status"] == "suppressed"
    assert result["data"] is None


@pytest.mark.asyncio
async def test_mcp_estimate_drug_cost_insulin_routed():
    result = await call_tool(
        "estimate_drug_cost",
        {"plan_key": PLAN_FL_PDP, "drug_name": "lantus"},
    )
    assert result["status"] == "insulin_out_of_scope"


@pytest.mark.asyncio
async def test_mcp_list_plans():
    result = await call_tool("list_plans", {"state": "FL"})
    assert result["status"] == "ok"
    assert any(p["plan_key"] == PLAN_FL_PDP for p in result["data"])


@pytest.mark.asyncio
async def test_mcp_unknown_tool():
    result = await call_tool("nonexistent_tool", {})
    assert result["status"] == "not_found"
