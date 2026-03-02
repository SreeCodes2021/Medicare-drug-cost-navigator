"""In-process MCP tool server for the Medicare Navigator."""

from __future__ import annotations

from medicare_navigator.mcp.registry import call_tool, tool_names
from medicare_navigator.mcp.schemas import anthropic_tools, openai_tools

__all__ = ["call_tool", "tool_names", "openai_tools", "anthropic_tools", "create_mcp_server"]


def create_mcp_server():
    """Create a FastMCP server exposing navigator tools (stdio/SSE for external agents)."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError("Install the mcp package to run the external MCP server.") from exc

    mcp = FastMCP("medicare-navigator")

    @mcp.tool()
    async def normalize_drug(drug_name: str, dosage: str | None = None) -> dict:
        """Resolve drug name to RxCUI and NDC."""
        return await call_tool("normalize_drug", {"drug_name": drug_name, "dosage": dosage})

    @mcp.tool()
    async def lookup_plan_tool(plan_key: str | None = None, search_text: str | None = None) -> dict:
        """Look up a Medicare plan."""
        return await call_tool(
            "lookup_plan", {"plan_key": plan_key, "search_text": search_text}
        )

    @mcp.tool()
    async def list_plans_tool(
        plan_type: str | None = None,
        state: str | None = None,
        contract_year: int | None = None,
    ) -> dict:
        """List available demo plans."""
        return await call_tool(
            "list_plans",
            {
                "plan_type": plan_type,
                "state": state,
                "contract_year": contract_year,
            },
        )

    @mcp.tool()
    async def formulary_benefit_lookup_tool(
        plan_key: str,
        ndc: str,
        ytd_oop_spend: float = 0.0,
        ytd_oop_spend_provided: bool = False,
        contract_year: int = 2026,
        quantity: int | None = None,
        fills: int | None = None,
        days_supply: int | None = 30,
    ) -> dict:
        """Formulary tier, cost-share, benefit phase, and supply estimate."""
        return await call_tool(
            "formulary_benefit_lookup",
            {
                "plan_key": plan_key,
                "ndc": ndc,
                "ytd_oop_spend": ytd_oop_spend,
                "ytd_oop_spend_provided": ytd_oop_spend_provided,
                "contract_year": contract_year,
                "quantity": quantity,
                "fills": fills,
                "days_supply": days_supply,
            },
        )

    @mcp.tool()
    async def cost_trend_lookup_tool(rxcui: str) -> dict:
        """Multi-year drug spending trend."""
        return await call_tool("cost_trend_lookup", {"rxcui": rxcui})

    @mcp.tool()
    async def alternatives_finder_tool(rxcui: str) -> dict:
        """Therapeutic equivalent alternatives."""
        return await call_tool("alternatives_finder", {"rxcui": rxcui})

    @mcp.tool()
    async def policy_retrieval_tool(query_text: str) -> dict:
        """Retrieve CMS policy passages."""
        return await call_tool("policy_retrieval", {"query_text": query_text})

    return mcp
