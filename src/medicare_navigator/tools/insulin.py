"""Insulin is priced via the statutory $35/30-day cap (tools/insulin_cost.py), not the
general tiered/deductible pipeline. No CMS SPUF field marks a drug as insulin, so this is
a hardcoded name/ingredient allowlist, mirroring the removed tools/ira_drugs.py pattern.
Includes GLP-1/insulin combination products (Soliqua, Xultophy), which are billed as a
single capped insulin product under Part D."""

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
        "lyumjev",
        "soliqua",
        "xultophy",
        # Biosimilars / additional brands found on live CMS formularies (allowlist audit).
        "rezvoglar",
        "afrezza",
        "insulin lispro-aabc",
        "insulin glargine-yfgn",
    }
)

INSULIN_FORMULARY_ALIASES: dict[str, tuple[str, ...]] = {
    "insulin glargine": ("lantus", "basaglar", "semglee", "toujeo", "rezvoglar"),
    "insulin lispro": ("humalog", "admelog", "lyumjev"),
    "insulin aspart": ("novolog", "fiasp"),
    "insulin degludec": ("tresiba",),
    "insulin detemir": ("levemir",),
    "insulin glulisine": ("apidra",),
    "insulin nph": ("novolin", "humulin"),
    "insulin regular": ("novolin", "humulin"),
}


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
