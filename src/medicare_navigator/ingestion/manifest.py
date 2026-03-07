"""Read and write data/manifest.json for ingestion freshness and source IDs."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from medicare_navigator.config import settings


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
