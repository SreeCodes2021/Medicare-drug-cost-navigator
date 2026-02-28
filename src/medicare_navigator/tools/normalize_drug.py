from __future__ import annotations

import json
from pathlib import Path

import httpx
import yaml

from medicare_navigator.config import settings
from medicare_navigator.models.tool_result import ToolResult, ToolStatus
from medicare_navigator.storage.repository import DrugRepository

SOURCE_ID = "rxnorm_api"
AS_OF_FALLBACK = "2026-01-15"


class NormalizeDrugData(dict):
    pass


def _manifest_as_of() -> str:
    manifest_path = settings.data_dir / "manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return data.get("rxnorm", {}).get("as_of", AS_OF_FALLBACK)
    return AS_OF_FALLBACK


async def _rxnorm_lookup(name: str) -> list[dict]:
    """Try RxNorm API; fall back to local DuckDB cache."""
    base = "https://rxnav.nlm.nih.gov/REST"
    candidates: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base}/rxcui.json", params={"name": name, "search": 2})
            if resp.status_code == 200:
                ids = resp.json().get("idGroup", {}).get("rxnormId", [])
                if isinstance(ids, str):
                    ids = [ids]
                for rxcui in ids[:3]:
                    candidates.append({"rxcui": rxcui, "name": name, "source": "rxnorm_api"})
    except httpx.HTTPError:
        pass

    if not candidates:
        repo = DrugRepository()
        records = repo.lookup_by_name(name)
        for r in records:
            candidates.append(
                {"rxcui": r.rxcui, "name": r.drug_name, "ndc": r.ndc, "dosage": r.dosage, "source": "local_cache"}
            )
    return candidates


async def normalize_drug(drug_name: str, dosage: str | None = None) -> ToolResult[dict]:
    as_of = _manifest_as_of()
    candidates = await _rxnorm_lookup(drug_name)

    if not candidates:
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message=f"No match found for drug name '{drug_name}'.",
        )

    repo = DrugRepository()
    enriched = []
    for c in candidates:
        record = repo.lookup_by_rxcui(c["rxcui"])
        if record:
            if dosage:
                dosage_norm = dosage.lower().replace(" ", "")
                if dosage_norm not in record.dosage.lower().replace(" ", ""):
                    continue
            enriched.append(
                {
                    "drug_name": record.drug_name,
                    "rxcui": record.rxcui,
                    "ndc": record.ndc,
                    "dosage": record.dosage,
                    "ingredient": record.ingredient,
                }
            )
        else:
            enriched.append(
                {
                    "drug_name": c.get("name", drug_name),
                    "rxcui": c["rxcui"],
                    "ndc": c.get("ndc"),
                    "dosage": c.get("dosage") or dosage,
                    "ingredient": c.get("name", drug_name),
                }
            )

    if not enriched:
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message=f"Drug '{drug_name}' found but no match for dosage '{dosage}'.",
        )

    return ToolResult.ok(
        {"candidates": enriched, "selected": enriched[0]},
        source_id=SOURCE_ID if enriched[0].get("source") != "local_cache" else "rxnorm_cache_demo",
        as_of_date=as_of,
    )


def load_benefit_params(contract_year: int = 2026) -> dict:
    path = settings.config_dir / "benefit_params.yaml"
    with path.open(encoding="utf-8") as f:
        params = yaml.safe_load(f)
    if params.get("contract_year") != contract_year:
        return params
    return params


def compute_benefit_phase(ytd_oop: float, deductible: float, oop_threshold: float) -> str:
    if ytd_oop < deductible:
        return "deductible"
    if ytd_oop < oop_threshold:
        return "initial_coverage"
    return "catastrophic"
