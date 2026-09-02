"""ZIP code centroid lookup and great-circle distance, backed by config/zip_centroids.csv
(US Census Bureau 2020 Gazetteer ZCTA file: GEOID, INTPTLAT, INTPTLONG columns).

This is a physical-location concept for the pharmacy locator feature — unrelated to
tools/zip_lookup.py's ZIP3->state table, which exists only to prefill a UI state picker
and must never influence drug-cost math. Distances here are straight-line (haversine)
between ZIP centroids, not driving distance.
"""

from __future__ import annotations

import csv
import math

from medicare_navigator.config import settings

_CENTROID_CACHE: dict[str, tuple[float, float]] | None = None

_EARTH_RADIUS_MILES = 3958.8


def _load_centroids() -> dict[str, tuple[float, float]]:
    global _CENTROID_CACHE
    if _CENTROID_CACHE is not None:
        return _CENTROID_CACHE
    path = settings.config_dir / "zip_centroids.csv"
    centroids: dict[str, tuple[float, float]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            zip5 = (row.get("zip") or "").strip()
            lat_raw = (row.get("lat") or "").strip()
            lon_raw = (row.get("lon") or "").strip()
            if not zip5 or not lat_raw or not lon_raw:
                continue
            try:
                centroids[zip5] = (float(lat_raw), float(lon_raw))
            except ValueError:
                continue
    _CENTROID_CACHE = centroids
    return centroids


def centroid_for_zip(zip5: str | None) -> tuple[float, float] | None:
    """Return (lat, lon) for a 5-digit ZIP, or None if unrecognized."""
    if not zip5:
        return None
    candidate = zip5.strip()
    if not (len(candidate) == 5 and candidate.isdigit()):
        return None
    return _load_centroids().get(candidate)


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two lat/lon points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_MILES * c


def distance_between_zips(zip_a: str, zip_b: str) -> float | None:
    """Straight-line miles between two ZIP centroids, or None if either is unrecognized."""
    point_a = centroid_for_zip(zip_a)
    point_b = centroid_for_zip(zip_b)
    if point_a is None or point_b is None:
        return None
    return haversine_miles(point_a[0], point_a[1], point_b[0], point_b[1])
