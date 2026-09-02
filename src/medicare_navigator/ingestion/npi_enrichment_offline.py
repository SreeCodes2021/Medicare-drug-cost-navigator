"""Curated NPPES NPI Registry snapshots for offline tests and degraded network mode.

Values are real, public NPPES NPI Registry (https://npiregistry.cms.hhs.gov/) records for
Florida-area pharmacies, captured for the pharmacy locator test fixtures. Used only when a
live NPPES lookup fails or returns no match — same role as tools/rxnorm_offline.py for
RxNorm.
"""

from __future__ import annotations

from typing import Any

# NPI -> {pharmacy_name, address_line1, city, state, zip_code, phone}
OFFLINE_NPI_DIRECTORY: dict[str, dict[str, Any]] = {
    "1841304730": {
        "pharmacy_name": "Icon Pharmacy",
        "address_line1": "300 E Church St",
        "city": "Orlando",
        "state": "FL",
        "zip_code": "32801",
        "phone": None,
    },
    "1952743569": {
        "pharmacy_name": "Angels Pharmacy I Inc",
        "address_line1": "259 E Michigan St",
        "city": "Orlando",
        "state": "FL",
        "zip_code": "32806",
        "phone": None,
    },
    "1174561328": {
        "pharmacy_name": "Albertsons LLC",
        "address_line1": "4300 220 Clarcona Ocoee Rd",
        "city": "Orlando",
        "state": "FL",
        "zip_code": "32810",
        "phone": None,
    },
    "1194289157": {
        "pharmacy_name": "Jackson Pharmacy Jackson South",
        "address_line1": "9333 SW 152nd Street",
        "city": "Miami",
        "state": "FL",
        "zip_code": "33157",
        "phone": "305-256-5182",
    },
    "1770916736": {
        "pharmacy_name": "Accredo Health Group Inc",
        "address_line1": "6272 Lee Vista Blvd",
        "city": "Orlando",
        "state": "FL",
        "zip_code": "32822",
        "phone": None,
    },
}


def offline_lookup(npi: str) -> dict[str, Any] | None:
    return OFFLINE_NPI_DIRECTORY.get(npi.strip())
