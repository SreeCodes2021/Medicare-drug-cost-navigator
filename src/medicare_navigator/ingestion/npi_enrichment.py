"""Enrich pharmacy NPIs (from the CMS SPUF pharmacy network file) with name/address via the
NPPES NPI Registry API — free, no auth: https://npiregistry.cms.hhs.gov/api/.

Ingest-time only (batch-fetches every distinct NPI discovered while loading the pharmacy
network file) and synchronous, matching ingestion/spuf.py's sync pipeline — unlike
tools/normalize_drug.py's RxNorm calls, which run live per chat query inside the async
request path and use httpx.AsyncClient. Falls back to ingestion/npi_enrichment_offline.py
on HTTP error or an empty match, same role as tools/rxnorm_offline.py for RxNorm.
"""

from __future__ import annotations

import httpx

from medicare_navigator.ingestion.npi_enrichment_offline import offline_lookup

_NPPES_BASE_URL = "https://npiregistry.cms.hhs.gov/api/"


def _parse_nppes_result(result: dict) -> dict[str, object] | None:
    basic = result.get("basic") or {}
    name = basic.get("organization_name") or basic.get("name")
    if not name:
        return None
    location = next(
        (a for a in result.get("addresses") or [] if a.get("address_purpose") == "LOCATION"),
        {},
    )
    postal = (location.get("postal_code") or "").strip()[:5]
    return {
        "pharmacy_name": name,
        "address_line1": location.get("address_1"),
        "city": location.get("city"),
        "state": location.get("state"),
        "zip_code": postal or None,
        "phone": location.get("telephone_number"),
    }


def _lookup_live(npi: str, *, client: httpx.Client) -> dict[str, object] | None:
    resp = client.get(_NPPES_BASE_URL, params={"number": npi, "version": "2.1"})
    if resp.status_code != 200:
        return None
    results = resp.json().get("results") or []
    if not results:
        return None
    return _parse_nppes_result(results[0])


def enrich_npis(npis: list[str]) -> dict[str, dict[str, object]]:
    """Return {npi: {pharmacy_name, address_line1, city, state, zip_code, phone, enrichment_source}}
    for every resolvable NPI. Unresolvable NPIs (live miss + no offline snapshot) are omitted."""
    enriched: dict[str, dict[str, object]] = {}
    try:
        with httpx.Client(timeout=10.0) as client:
            for npi in npis:
                record = None
                try:
                    record = _lookup_live(npi, client=client)
                except httpx.HTTPError:
                    record = None
                source = "nppes_api"
                if record is None:
                    record = offline_lookup(npi)
                    source = "nppes_offline"
                if record is not None:
                    enriched[npi] = {**record, "enrichment_source": source}
    except httpx.HTTPError:
        for npi in npis:
            if npi in enriched:
                continue
            record = offline_lookup(npi)
            if record is not None:
                enriched[npi] = {**record, "enrichment_source": "nppes_offline"}
    return enriched
