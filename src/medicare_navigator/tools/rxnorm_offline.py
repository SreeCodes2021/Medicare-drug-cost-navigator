"""Curated RxNorm snapshots for offline tests and degraded network mode.

Values mirror NLM RxNorm (2026) for demo/test drugs in COMMON_DRUGS plus insulin
fixtures. Used only when live RxNorm REST calls fail or return no matches.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

# Ingredient-level RXCUI (rxcui.json exact match).
OFFLINE_INGREDIENT_RXCUI: dict[str, str] = {
    "amlodipine": "17767",
    "atorvastatin": "83367",
    "escitalopram": "321988",
    "gabapentin": "25480",
    "humalog": "135805",
    "hydrochlorothiazide": "5487",
    "januvia": "638596",
    "lantus": "261551",
    "levothyroxine": "10582",
    "lisinopril": "29046",
    "losartan": "52175",
    "lovastatin": "6472",
    "metformin": "6809",
    "montelukast": "88249",
    "omeprazole": "7646",
    "pantoprazole": "40790",
    "rosuvastatin": "301542",
    "sertraline": "36437",
    "simvastatin": "36567",
}

# Strength-specific clinical-drug RXCUI keyed as "<drug>|<dosage>" (dosage lowercased, no spaces).
OFFLINE_DOSAGE_RXCUI: dict[str, str] = {
    "amlodipine|5mg": "197361",
    "atorvastatin|20mg": "617310",
    "lisinopril|10mg": "314076",
    "lovastatin|40mg": "197905",
    "metformin|500mg": "861007",
    "omeprazole|20mg": "198051",
    "simvastatin|20mg": "312961",
}

# SCD/SBD strength concepts for formulary expansion when the ingredient RXCUI misses.
OFFLINE_STRENGTH_CONCEPTS: dict[str, list[dict[str, str]]] = {
    "lantus": [
        {
            "rxcui": "285018",
            "tty": "SBD",
            "concept_name": "insulin glargine 100 UNT/ML Injectable Solution [Lantus]",
        },
        {
            "rxcui": "847232",
            "tty": "SBD",
            "concept_name": "3 ML insulin glargine 100 UNT/ML Pen Injector [Lantus]",
        },
    ],
    "humalog": [
        {
            "rxcui": "1652242",
            "tty": "SBD",
            "concept_name": "insulin lispro 100 UNT/ML Injectable Solution [Humalog]",
        },
    ],
    "januvia": [
        {
            "rxcui": "665036",
            "tty": "SBD",
            "concept_name": "sitagliptin phosphate 100 MG Oral Tablet [Januvia]",
        },
    ],
    "metformin": [
        {
            "rxcui": "861007",
            "tty": "SCD",
            "concept_name": "metformin hydrochloride 500 MG Oral Tablet",
        },
        {
            "rxcui": "861010",
            "tty": "SCD",
            "concept_name": "metformin hydrochloride 850 MG Oral Tablet",
        },
    ],
    "lisinopril": [
        {
            "rxcui": "314076",
            "tty": "SCD",
            "concept_name": "lisinopril 10 MG Oral Tablet",
        },
    ],
    "lovastatin": [
        {
            "rxcui": "197905",
            "tty": "SCD",
            "concept_name": "lovastatin 40 MG Oral Tablet",
        },
    ],
    "omeprazole": [
        {
            "rxcui": "198051",
            "tty": "SCD",
            "concept_name": "omeprazole 20 MG Oral Capsule",
        },
    ],
}


def _normalize_dosage_key(dosage: str) -> str:
    return re.sub(r"\s+", "", dosage.strip().lower())


def _dosage_lookup_key(drug_name: str, dosage: str) -> str:
    return f"{drug_name.strip().lower()}|{_normalize_dosage_key(dosage)}"


def offline_exact_lookup(name: str) -> list[dict[str, Any]]:
    key = name.strip().lower()
    rxcui = OFFLINE_INGREDIENT_RXCUI.get(key)
    if not rxcui:
        return []
    return [{"rxcui": rxcui, "name": key, "source": "rxnorm_offline"}]


def offline_strength_specific_lookup(name: str, dosage: str) -> list[dict[str, Any]]:
    key = name.strip().lower()
    dose_key = _dosage_lookup_key(key, dosage)
    rxcui = OFFLINE_DOSAGE_RXCUI.get(dose_key)
    if rxcui:
        return [
            {
                "rxcui": rxcui,
                "name": f"{key} {dosage}",
                "concept_name": f"{key} {dosage}",
                "source": "rxnorm_offline",
            }
        ]
    dosage_norm = _normalize_dosage_key(dosage)
    matches = []
    for concept in OFFLINE_STRENGTH_CONCEPTS.get(key, []):
        concept_name = concept.get("concept_name", "")
        if dosage_norm in re.sub(r"\s+", "", concept_name.lower()):
            matches.append(
                {
                    "rxcui": concept["rxcui"],
                    "name": concept_name,
                    "concept_name": concept_name,
                    "source": "rxnorm_offline",
                }
            )
    return matches


def offline_list_strength_concepts(name: str) -> list[dict[str, Any]]:
    key = name.strip().lower()
    concepts = OFFLINE_STRENGTH_CONCEPTS.get(key, [])
    return [
        {
            "rxcui": concept["rxcui"],
            "name": concept["concept_name"],
            "concept_name": concept["concept_name"],
            "source": "rxnorm_offline",
        }
        for concept in concepts
    ]


def offline_approximate_lookup(name: str, max_results: int = 5) -> list[dict[str, Any]]:
    key = name.strip().lower()
    candidates: list[dict[str, Any]] = []
    if key in OFFLINE_INGREDIENT_RXCUI:
        candidates.append(
            {
                "rxcui": OFFLINE_INGREDIENT_RXCUI[key],
                "name": key,
                "source": "rxnorm_offline",
            }
        )
    for match in difflib.get_close_matches(
        key, OFFLINE_INGREDIENT_RXCUI.keys(), n=max_results, cutoff=0.8
    ):
        if match == key:
            continue
        candidates.append(
            {
                "rxcui": OFFLINE_INGREDIENT_RXCUI[match],
                "name": match,
                "source": "rxnorm_offline",
            }
        )
    return candidates[:max_results]
