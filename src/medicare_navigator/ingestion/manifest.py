"""Read and write data/manifest.json for ingestion freshness and source IDs."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from medicare_navigator.config import settings

_SPUF_SOURCE_ID_RE = re.compile(r"^cms_spuf_(\d{4})_q(\d)$", re.IGNORECASE)


def manifest_path() -> Path:
    return settings.data_dir / "manifest.json"


def load_manifest() -> dict[str, Any]:
    path = manifest_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(manifest: dict[str, Any]) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def get_source_id(dataset: str, fallback: str) -> str:
    data = load_manifest()
    entry = data.get(dataset, {})
    if isinstance(entry, dict) and entry.get("source_id"):
        return str(entry["source_id"])
    return fallback


def get_as_of(dataset: str, fallback: str = "2026-01-15") -> str:
    data = load_manifest()
    entry = data.get(dataset, {})
    if isinstance(entry, dict) and entry.get("as_of"):
        return str(entry["as_of"])
    return fallback


def parse_spuf_source_id(source_id: str) -> tuple[int, int] | None:
    """Parse ``cms_spuf_2026_q1`` into ``(contract_year, quarter)``."""
    match = _SPUF_SOURCE_ID_RE.match(source_id.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def format_data_release_id(contract_year: int, quarter: int) -> str:
    return f"{contract_year}-Q{quarter}"


def calendar_quarter_from_date(value: date) -> int:
    return (value.month - 1) // 3 + 1


def quarter_from_iso_date(iso_date: str | None) -> int | None:
    if not iso_date:
        return None
    try:
        return calendar_quarter_from_date(date.fromisoformat(str(iso_date)[:10]))
    except ValueError:
        return None


def get_data_release() -> dict[str, Any] | None:
    """Active CMS SPUF release (YYYY-Qn) from manifest and ingested plan data."""
    manifest = load_manifest()
    spuf = manifest.get("spuf", {})
    contract_year: int | None = None
    quarter: int | None = None
    source_id: str | None = None
    as_of: str | None = None
    version: str | None = None
    seeded_at = get_seeded_at()

    if isinstance(spuf, dict):
        source_id = spuf.get("source_id")
        as_of = spuf.get("as_of")
        version = spuf.get("version")
        if spuf.get("contract_year") is not None:
            contract_year = int(spuf["contract_year"])
        if spuf.get("quarter") is not None:
            quarter = int(spuf["quarter"])
        if quarter is None and source_id:
            parsed = parse_spuf_source_id(str(source_id))
            if parsed:
                if contract_year is None:
                    contract_year = parsed[0]
                quarter = parsed[1]
        if quarter is None and seeded_at:
            quarter = quarter_from_iso_date(seeded_at)
        if quarter is None and as_of:
            quarter = quarter_from_iso_date(str(as_of))

    if contract_year is None:
        from medicare_navigator.storage.repository import PlanRepository

        years = PlanRepository().list_contract_years()
        if not years:
            return None
        contract_year = years[0]

    if quarter is None:
        quarter = quarter_from_iso_date(seeded_at) or 1

    release_id = format_data_release_id(contract_year, quarter)
    return {
        "id": release_id,
        "label": release_id,
        "contract_year": contract_year,
        "quarter": quarter,
        "source_id": source_id,
        "as_of": as_of,
        "version": version,
        "seeded_at": seeded_at,
    }


def list_data_releases() -> list[dict[str, Any]]:
    """CMS SPUF releases available in the local dataset (YYYY-Qn labels)."""
    release = get_data_release()
    return [release] if release else []


def get_contract_year(fallback: int = 2026) -> int:
    data = load_manifest()
    entry = data.get("benefit_params", {})
    if isinstance(entry, dict) and entry.get("contract_year"):
        return int(entry["contract_year"])
    spuf = data.get("spuf", {})
    if isinstance(spuf, dict) and spuf.get("contract_year"):
        return int(spuf["contract_year"])
    return fallback


def merge_manifest(updates: dict[str, Any]) -> dict[str, Any]:
    manifest = load_manifest()
    for key, value in updates.items():
        if key == "seeded_at":
            manifest[key] = value
            continue
        if isinstance(value, dict) and isinstance(manifest.get(key), dict):
            manifest[key] = {**manifest[key], **value}
        else:
            manifest[key] = value
    manifest["seeded_at"] = date.today().isoformat()
    save_manifest(manifest)
    return manifest


def get_seeded_at() -> str | None:
    """Return manifest seeded_at (YYYY-MM-DD) or None if missing."""
    seeded_at = load_manifest().get("seeded_at")
    return str(seeded_at) if seeded_at else None


def is_data_fresh(*, max_staleness_days: int = 1) -> bool:
    """
    True when manifest seeded_at is within max_staleness_days (inclusive) of today.

    Used by /api/health to surface whether the nightly ingest job likely succeeded.
    """
    seeded_at = get_seeded_at()
    if not seeded_at:
        return False
    try:
        seeded_date = date.fromisoformat(seeded_at)
    except ValueError:
        return False
    return (date.today() - seeded_date).days <= max_staleness_days


def data_freshness_summary(*, max_staleness_days: int = 1) -> dict[str, Any]:
    """Summary for health checks and deployment monitoring."""
    manifest = load_manifest()
    spuf = manifest.get("spuf", {}) if isinstance(manifest.get("spuf"), dict) else {}
    return {
        "seeded_at": get_seeded_at(),
        "data_fresh": is_data_fresh(max_staleness_days=max_staleness_days),
        "spuf_source_id": spuf.get("source_id"),
        "spuf_as_of": spuf.get("as_of"),
        "spuf_version": spuf.get("version"),
    }
