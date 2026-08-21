"""Find CMS-network pharmacies near a ZIP code, optionally scoped to one plan's network.

Distance is straight-line (haversine) between ZIP centroids, not driving distance — see
ingestion/zip_centroids.py. This ZIP is a physical-location input for the pharmacy locator
feature, a different concept than tools/zip_lookup.py's discovery-only ZIP3->state table;
it must never be passed into the drug-cost deductible/coverage-phase pipeline.

No live routing/driving-distance API, no real-time in-stock/availability data — CMS
pharmacy-network membership (enriched with NPPES name/address) only.
"""

from __future__ import annotations

from medicare_navigator.ingestion.manifest import get_as_of, get_source_id
from medicare_navigator.ingestion.zip_centroids import centroid_for_zip, haversine_miles
from medicare_navigator.models.response import PharmacyResult
from medicare_navigator.models.tool_result import ToolResult, ToolStatus
from medicare_navigator.storage.repository import PharmacyRepository

SOURCE_ID_FALLBACK = "cms_spuf_2026_q1"
AS_OF_FALLBACK = "2026-01-15"


def _source_id() -> str:
    return get_source_id("spuf", SOURCE_ID_FALLBACK)


def _as_of() -> str:
    return get_as_of("spuf", AS_OF_FALLBACK)


def _channel_for(
    preferred_yn: bool | None, retail_yn: bool | None, mail_yn: bool | None
) -> str | None:
    if preferred_yn is None:
        return None
    prefix = "preferred" if preferred_yn else "standard"
    if mail_yn and not retail_yn:
        return f"{prefix}_mail"
    return f"{prefix}_retail"


def is_zip_only_stub_pharmacy(
    *,
    pharmacy_name: str | None,
    address_line1: str | None,
    zip_code: str | None,
) -> bool:
    name = (pharmacy_name or "").strip()
    if address_line1:
        return False
    if not name.startswith("Pharmacy near "):
        return False
    suffix = name.removeprefix("Pharmacy near ").strip()
    return bool(suffix) and suffix == (zip_code or "")


def _apply_enriched_fields(pharmacy: PharmacyResult, record: dict[str, object]) -> PharmacyResult:
    return pharmacy.model_copy(
        update={
            "pharmacy_name": record.get("pharmacy_name") or pharmacy.pharmacy_name,
            "address_line1": record.get("address_line1"),
            "city": record.get("city"),
            "state": record.get("state"),
            "zip_code": record.get("zip_code") or pharmacy.zip_code,
        }
    )


def _enrich_stub_results(results: list[PharmacyResult]) -> list[PharmacyResult]:
    stub_npis = [
        pharmacy.npi
        for pharmacy in results
        if pharmacy.npi
        and is_zip_only_stub_pharmacy(
            pharmacy_name=pharmacy.pharmacy_name,
            address_line1=pharmacy.address_line1,
            zip_code=pharmacy.zip_code,
        )
    ]
    if not stub_npis:
        return results

    enriched = PharmacyRepository().enrich_stub_records(stub_npis)
    if not enriched:
        return results

    return [
        _apply_enriched_fields(pharmacy, enriched[pharmacy.npi])
        if pharmacy.npi in enriched
        else pharmacy
        for pharmacy in results
    ]


def find_pharmacies(
    *,
    zip_code: str,
    plan_key: str | None = None,
    preferred_only: bool | None = None,
    channel: str | None = None,
    radius_miles: float = 25,
    limit: int = 5,
) -> ToolResult[list[PharmacyResult]]:
    """Nearby pharmacies, sorted by distance. ``plan_key`` scopes to that plan's CMS network
    (Q1/Q2); omit it for a plan-agnostic locator search (Q3). ``channel`` filters to an exact
    computed channel (e.g. "preferred_retail") — used so "nearest preferred pharmacy" never
    resolves to a mail-order pharmacy, which has no meaningful physical proximity."""
    source_id = _source_id()
    as_of = _as_of()

    origin = centroid_for_zip(zip_code)
    if origin is None:
        return ToolResult.failure(
            ToolStatus.not_found,
            source_id=source_id,
            as_of_date=as_of,
            message=f"I don't recognize ZIP code '{zip_code}'.",
        )

    candidates = PharmacyRepository().nearby_candidates(
        plan_key=plan_key, preferred_only=preferred_only
    )

    results: list[PharmacyResult] = []
    for candidate in candidates:
        dest = centroid_for_zip(candidate.get("zip_code"))
        if dest is None:
            continue
        distance = haversine_miles(origin[0], origin[1], dest[0], dest[1])
        if distance > radius_miles:
            continue
        candidate_channel = _channel_for(
            candidate.get("preferred_yn"), candidate.get("retail_yn"), candidate.get("mail_yn")
        )
        if channel and candidate_channel != channel:
            continue
        results.append(
            PharmacyResult(
                npi=candidate["npi"],
                pharmacy_name=candidate["pharmacy_name"],
                address_line1=candidate.get("address_line1"),
                city=candidate.get("city"),
                state=candidate.get("state"),
                zip_code=candidate.get("zip_code"),
                distance_miles=round(distance, 1),
                preferred=candidate.get("preferred_yn"),
                channel=candidate_channel,
            )
        )

    results.sort(key=lambda r: r.distance_miles if r.distance_miles is not None else float("inf"))
    results = results[:limit]

    if not results:
        scope = f" in plan {plan_key}'s network" if plan_key else ""
        return ToolResult.failure(
            ToolStatus.no_match,
            source_id=source_id,
            as_of_date=as_of,
            message=f"No pharmacies{scope} found within {radius_miles:.0f} miles of ZIP {zip_code}.",
        )

    results = _enrich_stub_results(results)

    return ToolResult.ok(results, source_id=source_id, as_of_date=as_of)
