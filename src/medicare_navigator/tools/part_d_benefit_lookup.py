"""CMS Part D statutory benefit parameters exposed as an MCP tool."""

from __future__ import annotations

from medicare_navigator.ingestion.manifest import get_as_of
from medicare_navigator.models.tool_result import ToolResult
from medicare_navigator.tools.part_d_benefit_params import annual_oop_cap

SOURCE_ID = "cms_part_d_benefit_params"
AS_OF_FALLBACK = "2026-01-15"


def get_part_d_benefit_params(contract_year: int | None = None) -> ToolResult[dict]:
    """Return the statutory Part D annual out-of-pocket maximum for a contract year."""
    year = contract_year or 2026
    cap = annual_oop_cap(year)
    as_of = get_as_of("spuf", AS_OF_FALLBACK)
    return ToolResult.ok(
        {
            "contract_year": year,
            "annual_oop_cap": cap,
            "description": (
                "CMS statutory maximum out-of-pocket spending on covered Part D prescription "
                "drugs for the contract year (IRA redesign). Applies across Part D and MA-PD "
                "drug benefit; distinct from Medicare Advantage medical-network MOOP limits."
            ),
        },
        source_id=SOURCE_ID,
        as_of_date=as_of,
    )
