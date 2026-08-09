"""Load offline SPUF fixture data for tests (not used in production)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from medicare_navigator.config import settings
from medicare_navigator.ingestion.spuf import IngestFilters, ingest_spuf
from medicare_navigator.storage.connection import DuckDBConnection

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "spuf"
FIXTURE_INGEST_DATE = date(2026, 1, 15)

# Plan keys from tests/fixtures/spuf/
PLAN_FL_PDP = "S9999-001"
PLAN_FL_MAPD = "H8888-001"
PLAN_FL_MAPD_MOOP = "H5427-060"
PLAN_FL_SUPPRESSED = "S9999-003"
PLAN_FL_PARTIAL_CHANNELS = "S9999-004"

NDC_METFORMIN = "00093-7214-01"
NDC_METFORMIN_ALT = "00378-1805-02"
NDC_LISINOPRIL = "00378-1805-01"
NDC_LISINOPRIL_TIER2 = "00378-1805-99"
NDC_JANUVIA = "00006-0112-54"
NDC_OMEPRAZOLE = "00378-3590-77"
NDC_COINSURANCE_DRUG = "00002-1433-80"


def load_spuf_fixture(
    *,
    data_dir: Path,
    duckdb_path: Path | None = None,
) -> None:
    """Ingest minimal SPUF fixture into the given data directory."""
    duckdb_path = duckdb_path or data_dir / "navigator.duckdb"
    db = DuckDBConnection(path=duckdb_path)
    filters = IngestFilters(
        contract_year=2026,
        states=["FL"],
        pdp_region_codes={"FL": "11"},
        plan_type_prefixes=["S", "H"],
    )
    ingest_spuf(
        FIXTURE_DIR,
        filters=filters,
        db=db,
        version="SPUF.2026.20260115",
        preserve_non_spuf_tables=True,
    )


def patch_settings(monkeypatch, data_dir: Path, duckdb_path: Path | None = None) -> Path:
    """Point settings at a temp data dir and load the SPUF fixture."""
    monkeypatch.setattr(
        "medicare_navigator.ingestion.spuf.date",
        type("date", (), {"today": staticmethod(lambda: FIXTURE_INGEST_DATE)})(),
    )
    duckdb_path = duckdb_path or data_dir / "navigator.duckdb"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "duckdb_path", duckdb_path)
    load_spuf_fixture(data_dir=data_dir, duckdb_path=duckdb_path)
    return duckdb_path
