"""Load offline SPUF fixture data for tests (not used in production)."""

from __future__ import annotations

from pathlib import Path

from medicare_navigator.config import settings
from medicare_navigator.ingestion.policy_corpus import ingest_policy_corpus
from medicare_navigator.ingestion.spuf import IngestFilters, ingest_spuf
from medicare_navigator.storage.connection import DuckDBConnection

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "spuf"

# Plan keys from tests/fixtures/spuf/
PLAN_FL_PDP = "S9999-001"
PLAN_FL_MAPD = "H8888-001"
PLAN_TX_PDP = "S9999-002"

NDC_METFORMIN = "00093-7214-01"
NDC_LISINOPRIL = "00378-1805-01"
NDC_JANUVIA = "00006-0112-54"

# Minimal RxNorm cache for offline tests (production uses live RxNorm API).
TEST_DRUGS = [
    ("metformin", "6809", "00093-7214-01", "500mg", "metformin"),
    ("lisinopril", "29046", "00378-1805-01", "10mg", "lisinopril"),
    ("omeprazole", "7646", "00378-3590-77", "20mg", "omeprazole"),
    ("januvia", "593411", "00006-0112-54", "100mg", "sitagliptin"),
]


def _seed_test_drugs(db: DuckDBConnection) -> None:
    conn = db.connect()
    try:
        for row in TEST_DRUGS:
            conn.execute(
                "INSERT INTO drugs VALUES (?, ?, ?, ?, ?)",
                list(row),
            )
    finally:
        conn.close()


def seed_policy_corpus(
    data_dir: Path,
    duckdb_path: Path | None = None,
) -> None:
    """Load curated policy passages for offline retrieval tests."""
    duckdb_path = duckdb_path or data_dir / "navigator.duckdb"
    chroma_path = data_dir / "chroma"
    ingest_policy_corpus(
        db=DuckDBConnection(path=duckdb_path),
        chroma_path=chroma_path,
    )


def load_spuf_fixture(
    *,
    data_dir: Path,
    duckdb_path: Path | None = None,
    seed_policy: bool = True,
) -> None:
    """Ingest minimal SPUF fixture into the given data directory."""
    duckdb_path = duckdb_path or data_dir / "navigator.duckdb"
    db = DuckDBConnection(path=duckdb_path)
    filters = IngestFilters(
        contract_year=2026,
        states=["FL", "TX"],
        pdp_region_codes={"FL": "11", "TX": "22"},
        plan_type_prefixes=["S", "H"],
    )
    ingest_spuf(
        FIXTURE_DIR,
        filters=filters,
        db=db,
        version="SPUF.2026.20260115",
        preserve_non_spuf_tables=True,
    )
    _seed_test_drugs(db)
    if seed_policy:
        seed_policy_corpus(data_dir, duckdb_path)


def patch_settings(monkeypatch, data_dir: Path, duckdb_path: Path | None = None) -> Path:
    """Point settings at a temp data dir and load the SPUF fixture."""
    duckdb_path = duckdb_path or data_dir / "navigator.duckdb"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "duckdb_path", duckdb_path)
    monkeypatch.setattr(settings, "chroma_path", data_dir / "chroma")
    load_spuf_fixture(data_dir=data_dir, duckdb_path=duckdb_path)
    return duckdb_path
