"""Spec Section 3 step 2 / Section 6: insulin is out of scope for v1 (separate statutory
$35/month cap, separate CMS file, no benefit-phase dependency). No CMS SPUF field marks a
drug as insulin, so this is a hardcoded name/ingredient allowlist, mirroring the removed
tools/ira_drugs.py pattern."""

from __future__ import annotations

_INSULIN_NAMES: frozenset[str] = frozenset(
    {
        "insulin",
        "insulin aspart",
        "insulin glargine",
        "insulin glulisine",
        "insulin lispro",
        "insulin degludec",
        "insulin detemir",
        "insulin nph",
        "insulin regular",
        "humalog",
        "novolog",
        "novolin",
        "lantus",
        "toujeo",
        "levemir",
        "tresiba",
        "apidra",
        "fiasp",
        "basaglar",
        "semglee",
        "admelog",
        "humulin",
    }
)


def is_insulin(drug_name: str | None, ingredient: str | None = None) -> bool:
    for value in (drug_name, ingredient):
        if not value:
            continue
        lowered = value.strip().lower()
        if lowered in _INSULIN_NAMES:
            return True
        if any(name in lowered for name in _INSULIN_NAMES):
            return True
    return False
