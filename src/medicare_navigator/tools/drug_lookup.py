"""Drug/dosage discovery for guided-form pickers — UX only.

These helpers suggest drug names and available strengths from RxNorm. They are never
used to price estimates directly; /api/estimate* still runs normalize_drug on submit.
When a plan_id is provided, results include on_formulary hints for the hybrid picker UX.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import TypedDict

from medicare_navigator.storage.repository import BasicDrugsFormularyRepository, PlanRepository
from medicare_navigator.tools.normalize_drug import (
    _dosage_from_concept,
    _dosage_in_name,
    _rxnorm_approximate_lookup,
    list_strength_concepts,
)

# Curated starter list for empty-query browsing (common oral Part D drugs in demos/tests).
# Typical demo strengths — used to sort dosage clarification lists, not to auto-price.
COMMON_DEFAULT_STRENGTHS: dict[str, str] = {
    "amlodipine": "5mg",
    "atorvastatin": "20mg",
    "lisinopril": "10mg",
    "lovastatin": "40mg",
    "metformin": "500mg",
    "omeprazole": "20mg",
    "simvastatin": "20mg",
}

COMMON_DRUGS: tuple[str, ...] = (
    "amlodipine",
    "atorvastatin",
    "escitalopram",
    "gabapentin",
    "hydrochlorothiazide",
    "januvia",
    "levothyroxine",
    "lisinopril",
    "losartan",
    "lovastatin",
    "metformin",
    "montelukast",
    "omeprazole",
    "pantoprazole",
    "rosuvastatin",
    "sertraline",
    "simvastatin",
)

# Oral generics in COMMON_DRUGS where estimate must not auto-pick a strength.
COMMON_DRUGS_REQUIRING_DOSAGE: frozenset[str] = frozenset(
    drug for drug in COMMON_DRUGS if drug != "januvia"
)

_STRENGTH_CONCEPTS_CACHE: OrderedDict[str, list[dict]] = OrderedDict()
_STRENGTH_CONCEPTS_CACHE_MAX = 128


class DrugPickerItem(TypedDict):
    name: str
    on_formulary: bool


class DosagePickerItem(TypedDict):
    dosage: str
    on_formulary: bool


def ingredient_name_from_concept(concept_name: str) -> str | None:
    """Best-effort ingredient token from an RxNorm concept label."""
    text = (concept_name or "").strip()
    if not text:
        return None
    match = re.match(r"^([A-Za-z][A-Za-z0-9-]*)", text)
    if not match:
        return None
    return match.group(1).lower()


def _dosage_sort_key(dosage: str) -> tuple[float, str]:
    match = re.search(r"(\d+(?:\.\d+)?)", dosage)
    if match:
        return (float(match.group(1)), dosage.lower())
    return (float("inf"), dosage.lower())


async def _cached_list_strength_concepts(name: str) -> list[dict]:
    key = name.strip().lower()
    if key in _STRENGTH_CONCEPTS_CACHE:
        _STRENGTH_CONCEPTS_CACHE.move_to_end(key)
        return _STRENGTH_CONCEPTS_CACHE[key]
    concepts = await list_strength_concepts(name)
    _STRENGTH_CONCEPTS_CACHE[key] = concepts
    if len(_STRENGTH_CONCEPTS_CACHE) > _STRENGTH_CONCEPTS_CACHE_MAX:
        _STRENGTH_CONCEPTS_CACHE.popitem(last=False)
    return concepts


def _strength_rxcuis(concepts: list[dict], dosage: str | None = None) -> list[str]:
    rxcuis: list[str] = []
    for concept in concepts:
        concept_name = concept.get("concept_name") or concept.get("name") or ""
        if dosage and not _dosage_in_name(concept_name, dosage):
            continue
        rxcui = concept.get("rxcui")
        if rxcui:
            rxcuis.append(str(rxcui))
    return rxcuis


async def drug_on_formulary(plan_key: str, drug_name: str, dosage: str | None = None) -> bool | None:
    plan = PlanRepository().get_plan(plan_key)
    if not plan:
        return None
    formulary_id = plan.get("formulary_id")
    if not formulary_id:
        return None
    concepts = await _cached_list_strength_concepts(drug_name)
    rxcuis = _strength_rxcuis(concepts, dosage)
    if not rxcuis:
        return False
    return BasicDrugsFormularyRepository().has_any_rxcui(formulary_id, rxcuis)


async def search_drugs(
    query: str | None = None,
    *,
    limit: int = 30,
    plan_id: str | None = None,
) -> list[str] | list[DrugPickerItem]:
    """Return drug ingredient names for the picker, optionally filtered by query."""
    q = (query or "").strip().lower()
    seen: set[str] = set()
    results: list[str] = []

    def add(name: str) -> None:
        normalized = name.strip().lower()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        results.append(normalized)

    if not q:
        for drug in COMMON_DRUGS:
            add(drug)
    else:
        for drug in COMMON_DRUGS:
            if drug.startswith(q) or q in drug:
                add(drug)

        if len(results) < limit:
            for match in await _rxnorm_approximate_lookup(q, max_results=limit):
                ingredient = ingredient_name_from_concept(match.get("name", ""))
                if ingredient:
                    add(ingredient)

    names = results[:limit]
    if not plan_id:
        return names

    annotated: list[DrugPickerItem] = []
    for name in names:
        on_formulary = await drug_on_formulary(plan_id, name)
        annotated.append(
            {
                "name": name,
                "on_formulary": bool(on_formulary) if on_formulary is not None else False,
            }
        )
    return annotated


async def list_drug_dosages(
    drug_name: str,
    *,
    plan_id: str | None = None,
) -> list[str] | list[DosagePickerItem]:
    """Return unique dosage strings available for a drug (from RxNorm SCD/SBD concepts)."""
    name = (drug_name or "").strip()
    if not name:
        return []

    seen: set[str] = set()
    dosages: list[str] = []
    concepts_by_dosage: dict[str, list[dict]] = {}
    for concept in await _cached_list_strength_concepts(name):
        concept_name = concept.get("concept_name") or concept.get("name") or ""
        dosage = _dosage_from_concept(concept_name)
        if not dosage:
            continue
        key = dosage.lower()
        concepts_by_dosage.setdefault(key, []).append(concept)
        if key in seen:
            continue
        seen.add(key)
        dosages.append(dosage)

    dosages.sort(key=_dosage_sort_key)
    if not plan_id:
        return dosages

    plan = PlanRepository().get_plan(plan_id)
    formulary_id = plan.get("formulary_id") if plan else None
    repo = BasicDrugsFormularyRepository()
    annotated: list[DosagePickerItem] = []
    for dosage in dosages:
        on_formulary = False
        if formulary_id:
            concepts = concepts_by_dosage.get(dosage.lower(), [])
            rxcuis = _strength_rxcuis(concepts, dosage)
            on_formulary = repo.has_any_rxcui(formulary_id, rxcuis) if rxcuis else False
        annotated.append({"dosage": dosage, "on_formulary": on_formulary})
    return annotated
