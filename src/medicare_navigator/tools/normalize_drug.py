from __future__ import annotations

import json

import httpx

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


def _dosage_in_name(name: str, dosage: str) -> bool:
    return dosage.lower().replace(" ", "") in name.lower().replace(" ", "")


async def _rxnorm_strength_specific_lookup(name: str, dosage: str) -> list[dict]:
    """Resolve to the strength-specific clinical-drug RXCUI (RxNorm TTY SCD/SBD), which is
    what CMS SPUF formulary rows actually reference — the plain ingredient-level rxcui.json
    exact match (_rxnorm_exact_lookup) returns the ingredient concept only (e.g. "lovastatin"
    -> 6472), which will never match a formulary row keyed on "lovastatin 40 MG Oral Tablet"
    (197905). Without this, any dosage-qualified query would resolve to the wrong RXCUI and
    be reported as not covered even when the drug is on the formulary."""
    base = "https://rxnav.nlm.nih.gov/REST"
    matches: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base}/drugs.json", params={"name": name})
            if resp.status_code == 200:
                groups = resp.json().get("drugGroup", {}).get("conceptGroup") or []
                for group in groups:
                    tty = group.get("tty")
                    if tty not in ("SCD", "SBD"):
                        continue
                    for prop in group.get("conceptProperties") or []:
                        concept_name = prop.get("name") or ""
                        if _dosage_in_name(concept_name, dosage):
                            matches.append({"rxcui": prop.get("rxcui"), "tty": tty})
    except httpx.HTTPError:
        pass
    # Prefer generic (SCD) over branded (SBD) when both match the requested strength.
    matches.sort(key=lambda m: 0 if m["tty"] == "SCD" else 1)
    return [{"rxcui": m["rxcui"], "name": name, "source": "rxnorm_drugs_api"} for m in matches]


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


async def _rxnorm_lookup(name: str, dosage: str | None = None) -> list[dict]:
    """Try RxNorm API; fall back to local DuckDB cache.

    When a dosage is given, prefer the strength-specific clinical-drug RXCUI (matches CMS
    formulary rows) over the bare ingredient-level exact match.
    """
    if dosage:
        strength_matches = await _rxnorm_strength_specific_lookup(name, dosage)
        if strength_matches:
            return strength_matches

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
    candidates = await _rxnorm_lookup(drug_name, dosage)

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
        source_id=SOURCE_ID if enriched[0].get("source") != "local_cache" else "rxnorm_cache",
        as_of_date=as_of,
    )


def compute_benefit_phase(ytd_oop: float, deductible: float) -> str:
    """v1 scope: pre-deductible or initial-coverage only (no catastrophic phase)."""
    if ytd_oop < deductible:
        return "pre_deductible"
    return "initial_coverage"
