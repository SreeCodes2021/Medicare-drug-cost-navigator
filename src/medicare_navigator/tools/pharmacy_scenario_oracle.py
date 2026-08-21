"""Pharmacy-lookup scenario oracles for quality tests — compare chat prose to ``find_pharmacies``."""

from __future__ import annotations

from typing import Any

from medicare_navigator.tools.pharmacy_lookup import find_pharmacies

_NO_MATCH_PROSE_HINTS = (
    "no pharmacies",
    "no pharmacy",
    "don't recognize",
    "do not recognize",
    "not a recognized",
)


def build_pharmacy_lookup_oracle(spec: dict[str, Any]) -> dict[str, Any]:
    """Run ``find_pharmacies`` with the same parameters as a scenario expects."""
    result = find_pharmacies(
        zip_code=spec["zip_code"],
        plan_key=spec.get("plan_key"),
        preferred_only=spec.get("preferred_only"),
        channel=spec.get("channel"),
        radius_miles=float(spec.get("radius_miles", 25)),
        limit=int(spec.get("limit", 5)),
    )
    pharmacies: list[dict[str, Any]] = []
    if result.data:
        for pharmacy in result.data:
            pharmacies.append(
                {
                    "npi": pharmacy.npi,
                    "pharmacy_name": pharmacy.pharmacy_name,
                    "zip_code": pharmacy.zip_code,
                    "distance_miles": pharmacy.distance_miles,
                    "channel": pharmacy.channel,
                }
            )
    status = result.status.value if hasattr(result.status, "value") else str(result.status)
    return {
        "status": status,
        "message": result.message,
        "zip_code": spec["zip_code"],
        "pharmacies": pharmacies,
    }


def verify_pharmacy_prose_against_oracle(
    explanation: str,
    oracle: dict[str, Any],
    spec: dict[str, Any] | None = None,
) -> list[str]:
    """Return auto-check failure messages when prose diverges from the tool oracle."""
    spec = spec or {}
    failures: list[str] = []
    lower = explanation.lower()
    status = oracle.get("status")
    pharmacies: list[dict[str, Any]] = oracle.get("pharmacies") or []

    require_results = spec.get("require_results")
    require_no_match = spec.get("require_no_match")
    min_results = spec.get("min_results")

    if require_results and status != "ok":
        failures.append(
            "pharmacy oracle status="
            f"{status} (expected ok with results — is pharmacy network ingested for "
            f"ZIP {oracle.get('zip_code')}?)"
        )
        return failures

    if require_no_match and status == "ok" and pharmacies:
        failures.append("pharmacy oracle returned results but scenario expected no_match")
        return failures

    if min_results is not None and len(pharmacies) < min_results:
        failures.append(
            f"pharmacy oracle returned {len(pharmacies)} result(s), expected >= {min_results}"
        )

    if status == "ok" and pharmacies:
        if any(hint in lower for hint in _NO_MATCH_PROSE_HINTS):
            failures.append("prose claims no pharmacies but pharmacy oracle returned results")
        for pharmacy in pharmacies:
            name = (pharmacy.get("pharmacy_name") or "").strip()
            zip_code = (pharmacy.get("zip_code") or "").strip()
            name_ok = bool(name) and name.lower() in lower
            stub_ok = bool(zip_code) and f"pharmacy near {zip_code}".lower() in lower
            zip_ok = bool(zip_code) and zip_code in explanation
            if not (name_ok or stub_ok or zip_ok):
                label = name or f"ZIP {zip_code}"
                failures.append(f"oracle pharmacy not reflected in prose: {label}")
        max_distance = spec.get("max_distance_miles")
        if max_distance is not None:
            for pharmacy in pharmacies:
                distance = pharmacy.get("distance_miles")
                if distance is not None and distance > max_distance:
                    failures.append(
                        f"oracle pharmacy {pharmacy.get('pharmacy_name') or pharmacy.get('zip_code')} "
                        f"is {distance} mi away (> {max_distance} mi cap)"
                    )
    elif status in ("no_match", "not_found") or not pharmacies:
        if require_results:
            return failures
        if spec.get("match_oracle_no_match", True) and status == "no_match":
            if not any(hint in lower for hint in _NO_MATCH_PROSE_HINTS):
                failures.append("pharmacy oracle no_match but prose does not acknowledge it")

    for forbidden in spec.get("forbid_names_not_in_oracle") or []:
        forbidden_lower = forbidden.lower()
        oracle_names = {(p.get("pharmacy_name") or "").lower() for p in pharmacies}
        if forbidden_lower in lower and forbidden_lower not in oracle_names:
            failures.append(f"prose names pharmacy outside oracle: {forbidden}")

    if spec.get("require_radius_in_prose"):
        radius = float(spec.get("radius_miles", 25))
        radius_phrases = {
            f"within {radius:g} miles".lower(),
            f"within {int(radius)} miles".lower(),
        }
        if not any(phrase in lower for phrase in radius_phrases):
            failures.append(
                f"prose missing search radius (expected 'within {radius:g} miles')"
            )

    if spec.get("forbid_zero_mile_distance_prose"):
        for bad in ("0.0 mi away", "0 mi away", "0.0 miles", "0 miles"):
            if bad in lower:
                failures.append(f"prose shows zero-mile distance: {bad}")

    if spec.get("require_cross_zip_distance_in_prose"):
        cross_zip = [
            p for p in pharmacies if (p.get("distance_miles") or 0) > 0
        ]
        if cross_zip and " mi away" not in lower:
            failures.append(
                "prose missing cross-ZIP distance (expected 'X mi away' for pharmacies "
                "outside the query ZIP)"
            )

    if spec.get("forbid_any_distance_in_prose") and " mi away" in lower:
        failures.append(
            "prose shows distance but scenario expected same-ZIP-only results without miles"
        )

    return failures
