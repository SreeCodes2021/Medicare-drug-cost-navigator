from __future__ import annotations

import json

from medicare_navigator.config import settings
from medicare_navigator.models.response import AlternativesResult
from medicare_navigator.models.tool_result import ToolResult, ToolStatus
from medicare_navigator.storage.repository import AlternativesRepository, DrugRepository

SOURCE_ID = "fda_orange_book"


def _manifest_as_of() -> str:
    manifest_path = settings.data_dir / "manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return data.get("orange_book", {}).get("as_of", "2026-01-15")
    return "2026-01-15"


def alternatives_finder(rxcui: str, ingredient: str | None = None) -> ToolResult[list[AlternativesResult]]:
    as_of = _manifest_as_of()
    drug_repo = DrugRepository()
    record = drug_repo.lookup_by_rxcui(rxcui)
    ingredient = ingredient or (record.ingredient if record else None)

    if not ingredient:
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message="Cannot determine ingredient for alternatives lookup.",
        )

    repo = AlternativesRepository()
    alts = repo.find_alternatives(ingredient, exclude_rxcui=rxcui)
    if not alts:
        return ToolResult.failure(
            ToolStatus.no_match,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message=f"No therapeutically equivalent alternatives found for {ingredient}.",
        )

    results = [
        AlternativesResult(
            drug_name=a.drug_name,
            rxcui=a.rxcui,
            te_code=a.te_code,
            equivalent=True,
        )
        for a in alts
    ]
    return ToolResult.ok(results, source_id=SOURCE_ID, as_of_date=as_of)
