from pathlib import Path
import zipfile
from io import BytesIO

import pytest

from medicare_navigator.config import settings
from medicare_navigator.ingestion.spuf import IngestFilters, ingest_spuf
from medicare_navigator.models.tool_result import ToolStatus
from medicare_navigator.storage.connection import DuckDBConnection
from medicare_navigator.storage.repository import PlanRepository
from medicare_navigator.tools.formulary_benefit import formulary_benefit_lookup

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "spuf"


@pytest.fixture
def spuf_db(tmp_path, monkeypatch):
    db_path = tmp_path / "spuf_test.duckdb"
    manifest_path = tmp_path / "manifest.json"
    monkeypatch.setattr(settings, "duckdb_path", db_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return DuckDBConnection(path=db_path)


def test_ingest_spuf_fixture_loads_fl_tx_plans(spuf_db):
    filters = IngestFilters(
        contract_year=2026,
        states=["FL", "TX"],
        pdp_region_codes={"FL": "11", "TX": "22"},
        plan_type_prefixes=["S", "H"],
    )
    result = ingest_spuf(
        FIXTURE_DIR,
        filters=filters,
        db=spuf_db,
        version="SPUF.2026.20260115",
    )

    assert result["stats"]["plans"] == 3
    assert result["stats"]["formulary_rows"] >= 3
    assert result["source_id"] == "cms_spuf_2026_q1"

    repo = PlanRepository(db=spuf_db)
    fl_plans = repo.list_plans(state="FL")
    tx_plans = repo.list_plans(state="TX")
    assert len(fl_plans) == 2
    assert len(tx_plans) == 1
    assert any(p["plan_key"] == "S9999-001" for p in fl_plans)
    assert any(p["plan_key"] == "H8888-001" for p in fl_plans)
    assert tx_plans[0]["plan_key"] == "S9999-002"


def test_ingest_spuf_from_zip_archive(spuf_db, tmp_path):
    """Regression: zip must stay open while row iterator is consumed."""
    zip_path = tmp_path / "SPUF_2026_20260115.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for path in FIXTURE_DIR.iterdir():
            if path.is_file():
                zf.write(path, arcname=path.name)

    filters = IngestFilters(
        contract_year=2026,
        states=["FL", "TX"],
        pdp_region_codes={"FL": "11", "TX": "22"},
        plan_type_prefixes=["S", "H"],
    )
    result = ingest_spuf(
        zip_path,
        filters=filters,
        db=spuf_db,
        version="SPUF.2026.20260115",
    )
    assert result["stats"]["plans"] == 3


def test_ingest_spuf_from_nested_zip_members(spuf_db, tmp_path):
    """CMS quarterly SPUF wraps each pipe file in an inner .zip."""
    outer_path = tmp_path / "SPUF_2026_20260115.zip"
    with zipfile.ZipFile(outer_path, "w") as outer:
        for path in FIXTURE_DIR.iterdir():
            if not path.is_file():
                continue
            inner_name = f"{path.stem} PPUF_2026Q1.zip"
            inner_buf = BytesIO()
            with zipfile.ZipFile(inner_buf, "w") as inner:
                inner.write(path, arcname=path.name)
            outer.writestr(inner_name, inner_buf.getvalue())

    filters = IngestFilters(
        contract_year=2026,
        states=["FL", "TX"],
        pdp_region_codes={"FL": "11", "TX": "22"},
        plan_type_prefixes=["S", "H"],
    )
    result = ingest_spuf(
        outer_path,
        filters=filters,
        db=spuf_db,
        version="SPUF.2026.20260115",
    )
    assert result["stats"]["plans"] == 3


def test_formulary_lookup_accepts_11_digit_ndc(spuf_db, monkeypatch):
    filters = IngestFilters(
        contract_year=2026,
        states=["FL"],
        pdp_region_codes={"FL": "11", "TX": "22"},
        plan_type_prefixes=["S", "H"],
    )
    ingest_spuf(FIXTURE_DIR, filters=filters, db=spuf_db, version="SPUF.2026.20260115")
    monkeypatch.setattr(settings, "duckdb_path", spuf_db.path)
    monkeypatch.setattr(settings, "data_dir", spuf_db.path.parent)

    result = formulary_benefit_lookup("S9999-001", "00093721401")
    assert result.status == ToolStatus.ok
    assert result.data.tier == 1
    assert result.data.cost_share.copay == 5.0


def test_formulary_lookup_pharmacy_channel(spuf_db, monkeypatch):
    filters = IngestFilters(
        contract_year=2026,
        states=["FL"],
        pdp_region_codes={"FL": "11", "TX": "22"},
        plan_type_prefixes=["S", "H"],
    )
    ingest_spuf(FIXTURE_DIR, filters=filters, db=spuf_db, version="SPUF.2026.20260115")
    monkeypatch.setattr(settings, "duckdb_path", spuf_db.path)
    monkeypatch.setattr(settings, "data_dir", spuf_db.path.parent)

    preferred = formulary_benefit_lookup(
        "S9999-001", "00093-7214-01", pharmacy_channel="preferred_retail"
    )
    standard = formulary_benefit_lookup(
        "S9999-001", "00093-7214-01", pharmacy_channel="standard_retail"
    )
    assert preferred.data.cost_share.copay == 5.0
    assert standard.data.cost_share.copay == 15.0


def test_formulary_stale_when_contract_year_mismatch(spuf_db, monkeypatch):
    filters = IngestFilters(
        contract_year=2026,
        states=["FL"],
        pdp_region_codes={"FL": "11", "TX": "22"},
        plan_type_prefixes=["S", "H"],
    )
    ingest_spuf(FIXTURE_DIR, filters=filters, db=spuf_db, version="SPUF.2026.20260115")
    monkeypatch.setattr(settings, "duckdb_path", spuf_db.path)
    monkeypatch.setattr(settings, "data_dir", spuf_db.path.parent)

    result = formulary_benefit_lookup("S9999-001", "00093721401", contract_year=2025)
    assert result.status == ToolStatus.stale
    assert result.data is not None


def test_ingest_spuf_merge_states_fl_then_tx(spuf_db):
    filters_fl = IngestFilters(
        contract_year=2026,
        states=["FL"],
        pdp_region_codes={"FL": "11"},
        plan_type_prefixes=["S", "H"],
    )
    result_fl = ingest_spuf(
        FIXTURE_DIR,
        filters=filters_fl,
        db=spuf_db,
        version="SPUF.2026.20260115",
        merge_states=True,
    )
    assert result_fl["stats"]["plans"] == 2
    assert result_fl["stats"]["total_plans"] == 2

    filters_tx = IngestFilters(
        contract_year=2026,
        states=["TX"],
        pdp_region_codes={"TX": "22"},
        plan_type_prefixes=["S", "H"],
    )
    result_tx = ingest_spuf(
        FIXTURE_DIR,
        filters=filters_tx,
        db=spuf_db,
        version="SPUF.2026.20260115",
        merge_states=True,
    )
    assert result_tx["stats"]["plans"] == 1
    assert result_tx["stats"]["total_plans"] == 3
    assert result_tx["manifest"]["spuf"]["states"] == ["FL", "TX"]

    repo = PlanRepository(db=spuf_db)
    assert len(repo.list_plans(state="FL")) == 2
    assert len(repo.list_plans(state="TX")) == 1


def test_ingest_spuf_merge_states_replaces_same_state(spuf_db):
    filters = IngestFilters(
        contract_year=2026,
        states=["FL"],
        pdp_region_codes={"FL": "11"},
        plan_type_prefixes=["S", "H"],
    )
    ingest_spuf(
        FIXTURE_DIR,
        filters=filters,
        db=spuf_db,
        version="SPUF.2026.20260115",
        merge_states=True,
    )
    second = ingest_spuf(
        FIXTURE_DIR,
        filters=filters,
        db=spuf_db,
        version="SPUF.2026.20260115",
        merge_states=True,
    )
    assert second["stats"]["plans_purged"] == 2
    assert second["stats"]["total_plans"] == 2
    repo = PlanRepository(db=spuf_db)
    assert len(repo.list_plans(state="FL")) == 2
