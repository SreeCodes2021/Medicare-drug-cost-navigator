"""Demo allowlist of drugs selected for IRA Medicare price negotiation."""

from __future__ import annotations

IRA_NEGOTIATED_DRUG_NAMES = frozenset(
    {
        "eliquis",
        "apixaban",
        "januvia",
        "sitagliptin",
        "xarelto",
        "rivaroxaban",
        "farxiga",
        "dapagliflozin",
        "jardiance",
        "empagliflozin",
        "imbruvica",
        "ibrutinib",
        "stelara",
        "ustekinumab",
    }
)


def is_ira_negotiated(drug_name: str) -> bool:
    return drug_name.lower().strip() in IRA_NEGOTIATED_DRUG_NAMES
