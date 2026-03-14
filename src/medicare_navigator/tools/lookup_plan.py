from __future__ import annotations

from medicare_navigator.ingestion.manifest import get_as_of, get_source_id
from medicare_navigator.models.tool_result import ToolResult, ToolStatus
from medicare_navigator.storage.repository import PlanRepository

SOURCE_ID_FALLBACK = "cms_spuf_2026_q1"


def _source_id() -> str:
    return get_source_id("spuf", SOURCE_ID_FALLBACK)


def _manifest_as_of() -> str:
    return get_as_of("spuf", "2026-01-15")


def lookup_plan(
    plan_key: str | None = None,
    search_text: str | None = None,
) -> ToolResult[dict]:
    as_of = _manifest_as_of()
    source_id = _source_id()
    repo = PlanRepository()

    if plan_key:
        plan = repo.get_plan(plan_key)
        if plan:
            return ToolResult.ok(
                {"plan": plan, "candidates": [plan], "match_type": "exact"},
                source_id=source_id,
                as_of_date=as_of,
            )
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=source_id,
            as_of_date=as_of,
            message=f"Plan '{plan_key}' not found.",
        )

    text = (search_text or "").strip()
    if not text:
        return ToolResult.failure(
            ToolStatus.no_match,
            source_id=source_id,
            as_of_date=as_of,
            message="Provide plan_key or search_text.",
        )

    exact = repo.get_plan(text)
    if exact:
        return ToolResult.ok(
            {"plan": exact, "candidates": [exact], "match_type": "exact"},
            source_id=source_id,
            as_of_date=as_of,
        )

    candidates = repo.fuzzy_match_plan(text)
    if not candidates:
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=source_id,
            as_of_date=as_of,
            message=f"No plans matched '{text}'.",
        )

    if len(candidates) == 1:
        return ToolResult.ok(
            {"plan": candidates[0], "candidates": candidates, "match_type": "fuzzy"},
            source_id=source_id,
            as_of_date=as_of,
        )

    return ToolResult.ok(
        {"plan": None, "candidates": candidates, "match_type": "ambiguous"},
        source_id=source_id,
        as_of_date=as_of,
        message="Multiple plans matched; ask the user to choose.",
    )
