from __future__ import annotations

import json

from medicare_navigator.config import settings
from medicare_navigator.models.tool_result import ToolResult, ToolStatus
from medicare_navigator.storage.repository import PlanRepository

SOURCE_ID = "cms_spuf_2026_q1_demo"


def _manifest_as_of() -> str:
    manifest_path = settings.data_dir / "manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return data.get("spuf", {}).get("as_of", "2026-01-15")
    return "2026-01-15"


def lookup_plan(
    plan_key: str | None = None,
    search_text: str | None = None,
) -> ToolResult[dict]:
    as_of = _manifest_as_of()
    repo = PlanRepository()

    if plan_key:
        plan = repo.get_plan(plan_key)
        if plan:
            return ToolResult.ok(
                {"plan": plan, "candidates": [plan], "match_type": "exact"},
                source_id=SOURCE_ID,
                as_of_date=as_of,
            )
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message=f"Plan '{plan_key}' not found in demo plan set.",
        )

    text = (search_text or "").strip()
    if not text:
        return ToolResult.failure(
            ToolStatus.no_match,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message="Provide plan_key or search_text.",
        )

    exact = repo.get_plan(text)
    if exact:
        return ToolResult.ok(
            {"plan": exact, "candidates": [exact], "match_type": "exact"},
            source_id=SOURCE_ID,
            as_of_date=as_of,
        )

    candidates = repo.fuzzy_match_plan(text)
    if not candidates:
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message=f"No plans matched '{text}'.",
        )

    if len(candidates) == 1:
        return ToolResult.ok(
            {"plan": candidates[0], "candidates": candidates, "match_type": "fuzzy"},
            source_id=SOURCE_ID,
            as_of_date=as_of,
        )

    return ToolResult.ok(
        {"plan": None, "candidates": candidates, "match_type": "ambiguous"},
        source_id=SOURCE_ID,
        as_of_date=as_of,
        message="Multiple plans matched; ask the user to choose.",
    )
