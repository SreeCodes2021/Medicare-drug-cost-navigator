from datetime import date
from pathlib import Path

import pytest

from medicare_navigator.config import settings
from medicare_navigator.ingestion.spuf import IngestFilters, ingest_spuf
from medicare_navigator.storage.connection import DuckDBConnection
from medicare_navigator.tools.pharmacy_lookup import find_pharmacies
from medicare_navigator.tools.pharmacy_scenario_oracle import (
    build_pharmacy_lookup_oracle,
    verify_pharmacy_prose_against_oracle,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "spuf"


def _ingest_cms_network_at_zip(tmp_path: Path, *, zip_code: str, plan_key: str = "S9999-001") -> DuckDBConnection:
    contract, plan_id = plan_key.split("-")
    cms_network = tmp_path / "pharmacy networks file  PPUF_2026Q2 part 1.txt"
    cms_network.write_text(
        "CONTRACT_ID|PLAN_ID|SEGMENT_ID|PHARMACY_NUMBER|PHARMACY_ZIPCODE|"
        "PREFERRED_STATUS_RETAIL|PREFERRED_STATUS_MAIL|PHARMACY_RETAIL|PHARMACY_MAIL\n"
        f"{contract}|{plan_id}|000|101689685109|{zip_code}|N|N|Y|N\n"
        f"{contract}|{plan_id}|000|101710355243|72701|N|N|Y|N\n",
        encoding="utf-8",
    )
    for path in FIXTURE_DIR.iterdir():
        if path.is_file() and path.name != "pharmacy network file.txt":
            target = tmp_path / path.name
            if not target.exists():
                target.write_bytes(path.read_bytes())

    db = DuckDBConnection(path=tmp_path / "navigator.duckdb")
    ingest_spuf(
        tmp_path,
        filters=IngestFilters(
            contract_year=2026,
            states=["FL"],
            pdp_region_codes={"FL": "11"},
            plan_type_prefixes=["S", "H"],
        ),
        db=db,
        version="SPUF.2026.20260115",
    )
    return db


def test_build_pharmacy_lookup_oracle_returns_tool_results(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "duckdb_path", tmp_path / "navigator.duckdb")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _ingest_cms_network_at_zip(tmp_path, zip_code="72712")

    oracle = build_pharmacy_lookup_oracle({"zip_code": "72712", "limit": 5})
    assert oracle["status"] == "ok"
    assert len(oracle["pharmacies"]) >= 1
    assert oracle["pharmacies"][0]["zip_code"] == "72712"


def test_verify_pharmacy_prose_flags_false_no_match_when_oracle_has_results():
    oracle = {
        "status": "ok",
        "zip_code": "72712",
        "pharmacies": [{"pharmacy_name": "Pharmacy near 72712", "zip_code": "72712", "distance_miles": 0.0}],
    }
    failures = verify_pharmacy_prose_against_oracle(
        "No pharmacies were found within 25 miles of ZIP 72712.",
        oracle,
        {"zip_code": "72712", "require_results": True, "min_results": 1},
    )
    assert failures
    assert any("no pharmacies" in issue for issue in failures)


def test_verify_pharmacy_prose_accepts_oracle_pharmacy_labels():
    oracle = {
        "status": "ok",
        "zip_code": "72712",
        "pharmacies": [{"pharmacy_name": "Pharmacy near 72712", "zip_code": "72712", "distance_miles": 0.0}],
    }
    prose = (
        "Pharmacies near ZIP 72712:\n\n"
        "- Pharmacy near 72712 — 72712 — 0.0 mi away\n"
    )
    failures = verify_pharmacy_prose_against_oracle(
        prose,
        oracle,
        {"zip_code": "72712", "require_results": True, "min_results": 1},
    )
    assert failures == []


def test_find_pharmacies_cms_same_zip_pharmacy_is_zero_miles(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "duckdb_path", tmp_path / "navigator.duckdb")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _ingest_cms_network_at_zip(tmp_path, zip_code="72712")

    result = find_pharmacies(zip_code="72712", limit=5)
    assert result.status.value == "ok"
    assert result.data
    assert any(p.zip_code == "72712" and p.distance_miles == 0.0 for p in result.data)
    assert all(p.distance_miles <= 25 for p in result.data)


def test_find_pharmacies_cms_oracle_excludes_fl_fixture_when_only_cms_rows_loaded(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "duckdb_path", tmp_path / "navigator.duckdb")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _ingest_cms_network_at_zip(tmp_path, zip_code="72712")

    result = find_pharmacies(zip_code="72712", limit=5)
    names = {p.pharmacy_name for p in result.data or []}
    assert "Icon Pharmacy" not in names
