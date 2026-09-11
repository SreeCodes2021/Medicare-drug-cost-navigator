from __future__ import annotations

import difflib
import json
import re

import httpx

from medicare_navigator.config import settings
from medicare_navigator.models.tool_result import ToolResult, ToolStatus
from medicare_navigator.tools.rxnorm_offline import (
    offline_approximate_lookup,
    offline_exact_lookup,
    offline_list_strength_concepts,
    offline_strength_specific_lookup,
)

SOURCE_ID = "rxnorm_api"
AS_OF_FALLBACK = "2026-01-15"

# Non-English / alternate spellings → canonical English ingredient (COMMON_DRUGS keys).
DRUG_NAME_ALIASES: dict[str, str] = {
    "metformina": "metformin",
    "lisinoprilo": "lisinopril",
    "atorvastatina": "atorvastatin",
    "losartán": "losartan",
    "omeprazol": "omeprazole",
    "simvastatina": "simvastatin",
    "lovastatina": "lovastatin",
    "gabapentina": "gabapentin",
}


def canonicalize_drug_name(name: str) -> str:
    """Map aliases and close typos to a canonical English ingredient before RxNorm lookup."""
    from medicare_navigator.tools.drug_lookup import COMMON_DRUGS

    collapsed = re.sub(r"\s+", " ", name.strip())
    if not collapsed:
        return name.strip()
    key = collapsed.lower()
    if key in DRUG_NAME_ALIASES:
        return DRUG_NAME_ALIASES[key]
    if key in COMMON_DRUGS:
        return key
    for token in re.findall(r"[a-zA-Z]+", key):
        if token in DRUG_NAME_ALIASES:
            return DRUG_NAME_ALIASES[token]
        if token in COMMON_DRUGS:
            return token
    for token in re.findall(r"[a-zA-Z]+", key):
        close = difflib.get_close_matches(token, COMMON_DRUGS, n=1, cutoff=0.82)
        if close:
            return close[0]
    close = difflib.get_close_matches(key, COMMON_DRUGS, n=1, cutoff=0.82)
    if close:
        return close[0]
    return collapsed


class NormalizeDrugData(dict):
    pass


def _manifest_as_of() -> str:
    manifest_path = settings.data_dir / "manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return data.get("rxnorm", {}).get("as_of", AS_OF_FALLBACK)
    return AS_OF_FALLBACK


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
    if not candidates:
        candidates = offline_exact_lookup(name)
    return candidates


def _dosage_in_name(name: str, dosage: str) -> bool:
    return dosage.lower().replace(" ", "") in name.lower().replace(" ", "")


async def list_strength_concepts(name: str) -> list[dict]:
    """All single-ingredient SCD/SBD strength concepts for a drug name (RxNorm /drugs.json)."""
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
                        if " / " in concept_name:
                            continue
                        matches.append(
                            {
                                "rxcui": prop.get("rxcui"),
                                "tty": tty,
                                "concept_name": concept_name,
                            }
                        )
    except httpx.HTTPError:
        pass
    if not matches:
        matches = [
            {
                "rxcui": concept["rxcui"],
                "tty": concept.get("tty", "SCD"),
                "concept_name": concept.get("concept_name") or concept.get("name") or name,
            }
            for concept in offline_list_strength_concepts(name)
        ]
    name_lower = name.lower()

    def _strength_rank(m: dict) -> tuple[int, int, int]:
        concept = (m.get("concept_name") or "").lower()
        starts_with_ingredient = 0 if concept.startswith(name_lower) else 1
        scd_first = 0 if m["tty"] == "SCD" else 1
        branded_suffix = 1 if "[" in concept else 0
        return (starts_with_ingredient, scd_first, branded_suffix)

    matches.sort(key=_strength_rank)
    return [
        {
            "rxcui": m["rxcui"],
            "name": m.get("concept_name") or name,
            "concept_name": m.get("concept_name"),
            "source": "rxnorm_drugs_api",
        }
        for m in matches
    ]


async def _rxnorm_strength_specific_lookup(name: str, dosage: str) -> list[dict]:
    """Resolve to the strength-specific clinical-drug RXCUI (RxNorm TTY SCD/SBD), which is
    what CMS SPUF formulary rows actually reference — the plain ingredient-level rxcui.json
    exact match (_rxnorm_exact_lookup) returns the ingredient concept only (e.g. "lovastatin"
    -> 6472), which will never match a formulary row keyed on "lovastatin 40 MG Oral Tablet"
    (197905). Without this, any dosage-qualified query would resolve to the wrong RXCUI and
    be reported as not covered even when the drug is on the formulary."""
    strength_matches = [
        match
        for match in await list_strength_concepts(name)
        if _dosage_in_name(match.get("concept_name") or "", dosage)
    ]
    if strength_matches:
        return strength_matches
    return offline_strength_specific_lookup(name, dosage)


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
    if not candidates:
        candidates = offline_approximate_lookup(name, max_results=max_results)
    return candidates


async def _collect_drug_candidates(name: str, dosage: str | None = None) -> list[dict]:
    seen: set[str] = set()
    candidates: list[dict] = []

    def add_candidate(candidate: dict) -> None:
        key = candidate.get("rxcui") or candidate.get("name", "")
        if not key or key in seen:
            return
        seen.add(key)
        candidates.append(candidate)

    for match in await _rxnorm_approximate_lookup(name):
        add_candidate(
            {
                "drug_name": match.get("name", name),
                "rxcui": match["rxcui"],
                "dosage": dosage,
                "ingredient": match.get("name", name),
                "source": match.get("source", "rxnorm_approximate"),
            }
        )

    return candidates[:5]


async def _rxnorm_lookup(name: str, dosage: str | None = None) -> list[dict]:
    """Resolve drug names via the live RxNorm REST API only.

    When a dosage is given, prefer the strength-specific clinical-drug RXCUI (matches CMS
    formulary rows) over the bare ingredient-level exact match.
    """
    if dosage:
        strength_matches = await _rxnorm_strength_specific_lookup(name, dosage)
        if strength_matches:
            return strength_matches

    candidates = await _rxnorm_exact_lookup(name)
    if not candidates:
        for match in await _rxnorm_approximate_lookup(name):
            candidates.append(
                {
                    "rxcui": match["rxcui"],
                    "name": match.get("name", name),
                    "source": "rxnorm_approximate",
                }
            )
    return candidates


def _dosage_from_concept(concept_name: str) -> str | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*MG", concept_name, re.I)
    if match:
        return f"{match.group(1)}mg"
    return None


async def dosage_candidates_for_drug(drug_name: str, *, limit: int = 8) -> list[str]:
    """Strength options for clarification when the user omits dosage."""
    from medicare_navigator.tools.drug_lookup import COMMON_DEFAULT_STRENGTHS

    canonical = canonicalize_drug_name(drug_name)
    dosages: list[str] = []
    seen: set[str] = set()
    for concept in await list_strength_concepts(canonical):
        strength = _dosage_from_concept(concept.get("concept_name") or "")
        if strength and strength not in seen:
            seen.add(strength)
            dosages.append(strength)
    default = COMMON_DEFAULT_STRENGTHS.get(canonical)
    if default and default in dosages:
        dosages.remove(default)
        dosages.insert(0, default)
    return dosages[:limit]


def _needs_dosage_result(
    drug_name: str,
    candidates: list[str],
    *,
    as_of: str,
) -> ToolResult[dict]:
    canonical = canonicalize_drug_name(drug_name)
    strengths = ", ".join(candidates)
    return ToolResult.failure(
        ToolStatus.needs_dosage,
        source_id=SOURCE_ID,
        as_of_date=as_of,
        message=(
            f"Strength (dosage) is required to estimate '{canonical}'. "
            f"Common strengths: {strengths}. Please specify one before estimating."
        ),
        data={"drug_name": canonical, "dosage_candidates": candidates, "query": drug_name},
    )


def _split_strength_from_drug_name(drug_name: str, dosage: str | None) -> tuple[str, str | None]:
    if dosage:
        return drug_name.strip(), dosage
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*mg\b", drug_name, re.I)
    if not match:
        return drug_name.strip(), None
    strength = f"{match.group(1)}mg"
    base = re.sub(r"\s*\d+(?:\.\d+)?\s*mg\b", "", drug_name, count=1, flags=re.I).strip()
    return base or drug_name.strip(), strength


async def normalize_drug(drug_name: str, dosage: str | None = None) -> ToolResult[dict]:
    as_of = _manifest_as_of()
    drug_name, dosage = _split_strength_from_drug_name(drug_name, dosage)
    drug_name = canonicalize_drug_name(drug_name)
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

    enriched = []
    for c in candidates:
        concept_name = c.get("concept_name") or c.get("name") or drug_name
        if dosage and c.get("source") != "rxnorm_drugs_api":
            if not _dosage_in_name(concept_name, dosage):
                continue
        enriched.append(
            {
                "drug_name": drug_name,
                "rxcui": c["rxcui"],
                "ndc": c.get("ndc"),
                "dosage": dosage or _dosage_from_concept(concept_name),
                "ingredient": drug_name,
            }
        )

    if not enriched:
        near_misses = await _collect_drug_candidates(drug_name, dosage)
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=SOURCE_ID,
            as_of_date=as_of,
            message=f"Drug '{drug_name}' found but no match for dosage '{dosage}'.",
            data={"candidates": near_misses, "query": drug_name, "dosage": dosage},
        )

    return ToolResult.ok(
        {"candidates": enriched, "selected": enriched[0]},
        source_id=SOURCE_ID,
        as_of_date=as_of,
    )


def compute_benefit_phase(
    ytd_oop: float,
    deductible: float,
    *,
    contract_year: int = 2026,
) -> str:
    from medicare_navigator.tools.part_d_benefit_params import annual_oop_cap

    if ytd_oop >= annual_oop_cap(contract_year):
        return "catastrophic"
    if ytd_oop < deductible:
        return "pre_deductible"
    return "initial_coverage"
