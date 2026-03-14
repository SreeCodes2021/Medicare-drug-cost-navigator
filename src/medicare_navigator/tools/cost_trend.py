from __future__ import annotations

import json

from medicare_navigator.config import settings
from medicare_navigator.models.response import CostTrendPoint
from medicare_navigator.models.tool_result import ToolResult, ToolStatus
from medicare_navigator.storage.repository import CostTrendRepository

SOURCE_ID = "cms_part_d_spending"


def _manifest_as_of() -> str:
    manifest_path = settings.data_dir / "manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return data.get("spending", {}).get("as_of", "2026-01-15")
    return "2026-01-15"


def cost_trend_lookup(rxcui: str) -> ToolResult[list[CostTrendPoint]]:
    as_of = _manifest_as_of()
    repo = CostTrendRepository()
    records = repo.get_trend(rxcui)
    if not records:
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message=f"No cost trend data for RxCUI {rxcui}.",
        )

    points = [
        CostTrendPoint(year=r.year, total_spend=r.total_spend, avg_unit_cost=r.avg_unit_cost)
        for r in records
    ]
    return ToolResult.ok(points, source_id=SOURCE_ID, as_of_date=as_of)
