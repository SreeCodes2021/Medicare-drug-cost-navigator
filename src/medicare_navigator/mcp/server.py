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
    async def estimate_drug_cost_tool(
        plan_key: str,
        drug_name: str,
        dosage: str | None = None,
        days_supply: int = 30,
        ytd_oop_spend: float = 0.0,
        pharmacy_channel: str = "preferred_retail",
    ) -> dict:
        """Estimate the out-of-pocket cost of a single drug fill on a Medicare plan."""
        return await call_tool(
            "estimate_drug_cost",
            {
                "plan_key": plan_key,
                "drug_name": drug_name,
                "dosage": dosage,
                "days_supply": days_supply,
                "ytd_oop_spend": ytd_oop_spend,
                "pharmacy_channel": pharmacy_channel,
            },
        )

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
        """List available Medicare plans."""
        return await call_tool(
            "list_plans",
            {
                "plan_type": plan_type,
                "state": state,
                "contract_year": contract_year,
            },
        )

    return mcp
