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


def _record_to_candidate(record, source: str = "local_cache") -> dict:
    return {
        "drug_name": record.drug_name,
        "rxcui": record.rxcui,
        "ndc": record.ndc,
        "dosage": record.dosage,
        "ingredient": record.ingredient,
        "source": source,
    }


async def _rxnorm_exact_lookup(name: str) -> list[dict]:
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
    return candidates


async def _rxnorm_approximate_lookup(name: str, max_results: int = 5) -> list[dict]:
    base = "https://rxnav.nlm.nih.gov/REST"
    candidates: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base}/approximateTerm.json", params={"term": name, "maxEntries": max_results})
            if resp.status_code == 200:
                entries = resp.json().get("approximateGroup", {}).get("candidate", [])
                if isinstance(entries, dict):
                    entries = [entries]
                for entry in entries[:max_results]:
                    rxcui = entry.get("rxcui")
                    if not rxcui:
                        continue
                    candidates.append(
                        {
                            "rxcui": str(rxcui),
                            "name": entry.get("name", name),
                            "source": "rxnorm_approximate",
                            "score": entry.get("score"),
                        }
                    )
    except httpx.HTTPError:
        pass
    return candidates


def _local_candidates(name: str, dosage: str | None = None) -> list[dict]:
    repo = DrugRepository()
    records = repo.lookup_by_name(name, dosage)
    return [_record_to_candidate(r) for r in records[:5]]


async def _collect_drug_candidates(name: str, dosage: str | None = None) -> list[dict]:
    seen: set[str] = set()
    candidates: list[dict] = []

    def add_candidate(candidate: dict) -> None:
        key = candidate.get("rxcui") or candidate.get("drug_name", "")
        if not key or key in seen:
            return
        seen.add(key)
        candidates.append(candidate)

    for record in _local_candidates(name, dosage):
        add_candidate(record)

    for match in await _rxnorm_approximate_lookup(name):
        rxcui = match.get("rxcui")
        if not rxcui:
            continue
        repo = DrugRepository()
        record = repo.lookup_by_rxcui(rxcui)
        if record:
            add_candidate(_record_to_candidate(record, source="rxnorm_approximate"))
        else:
            add_candidate(
                {
                    "drug_name": match.get("name", name),
                    "rxcui": rxcui,
                    "dosage": dosage,
                    "ingredient": match.get("name", name),
                    "source": "rxnorm_approximate",
                }
            )

    if len(candidates) < 3 and len(name) >= 4:
        prefix = name[: max(4, len(name) - 1)]
        for record in _local_candidates(prefix, dosage):
            add_candidate(record)

    return candidates[:5]


async def _rxnorm_lookup(name: str) -> list[dict]:
    """Try RxNorm API; fall back to local DuckDB cache."""
    candidates = await _rxnorm_exact_lookup(name)
    if not candidates:
        for record in _local_candidates(name):
            candidates.append(
                {
                    "rxcui": record["rxcui"],
                    "name": record["drug_name"],
                    "ndc": record["ndc"],
                    "dosage": record["dosage"],
                    "source": "local_cache",
                }
            )
    return candidates


async def normalize_drug(drug_name: str, dosage: str | None = None) -> ToolResult[dict]:
    as_of = _manifest_as_of()
    candidates = await _rxnorm_lookup(drug_name)

    if not candidates:
        near_misses = await _collect_drug_candidates(drug_name, dosage)
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message=f"No match found for drug name '{drug_name}'.",
            data={"candidates": near_misses, "query": drug_name},
        )

    repo = DrugRepository()
    enriched = []
    for c in candidates:
        record = repo.lookup_by_rxcui(c["rxcui"])
        if not record:
            local_matches = repo.lookup_by_name(drug_name, dosage)
            if local_matches:
                record = local_matches[0]
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
        near_misses = await _collect_drug_candidates(drug_name)
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message=f"Drug '{drug_name}' found but no match for dosage '{dosage}'.",
            data={"candidates": near_misses, "query": drug_name, "dosage": dosage},
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
