from pathlib import Path
import zipfile
from io import BytesIO

import pytest

from medicare_navigator.config import settings
from medicare_navigator.ingestion.schema import create_indexes, create_tables
from medicare_navigator.ingestion.spuf import IngestFilters, _purge_states, ingest_spuf
from medicare_navigator.storage.connection import DuckDBConnection
from medicare_navigator.storage.repository import PlanRepository

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "spuf"


@pytest.fixture
def spuf_db(tmp_path, monkeypatch):
    db_path = tmp_path / "spuf_test.duckdb"
    monkeypatch.setattr(settings, "duckdb_path", db_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return DuckDBConnection(path=db_path)


def _all_states_filters(**overrides) -> IngestFilters:
    defaults = dict(
        contract_year=2026,
        states=["FL", "TX"],
        pdp_region_codes={"FL": "11", "TX": "22"},
        plan_type_prefixes=["S", "H"],
    )
    defaults.update(overrides)
    return IngestFilters(**defaults)


def test_ingest_spuf_fixture_loads_fl_tx_plans(spuf_db):
    result = ingest_spuf(
        FIXTURE_DIR,
        filters=_all_states_filters(),
        db=spuf_db,
        version="SPUF.2026.20260115",
    )

    # 4 plans total: S9999-001 (FL), S9999-002 (TX), H8888-001 (FL), S9999-003 (FL, suppressed)
    assert result["stats"]["plans"] == 4
    assert result["stats"]["formulary_rows"] >= 3
    assert result["source_id"] == "cms_spuf_2026_q1"

    repo = PlanRepository(db=spuf_db)
    fl_plans = repo.list_plans(state="FL")
    tx_plans = repo.list_plans(state="TX")
    assert len(fl_plans) == 3
    assert len(tx_plans) == 1
    assert any(p["plan_key"] == "S9999-001" for p in fl_plans)
    assert any(p["plan_key"] == "H8888-001" for p in fl_plans)
    assert tx_plans[0]["plan_key"] == "S9999-002"


def test_suppressed_plan_is_ingested_not_filtered(spuf_db):
    """Bug 6: suppressed plans must still be selectable, not silently dropped at ingest."""
    ingest_spuf(FIXTURE_DIR, filters=_all_states_filters(), db=spuf_db, version="SPUF.2026.20260115")
    repo = PlanRepository(db=spuf_db)
    plan = repo.get_plan("S9999-003")
    assert plan is not None
    assert plan["plan_suppressed"] is True

    other = repo.get_plan("S9999-001")
    assert other is not None
    assert other["plan_suppressed"] is False


def test_formulary_version_dedup_keeps_max_version(spuf_db):
    """FORM0001 has a stale version-00000 row (tier=9, bogus) that must be dropped in favor
    of version 00001."""
    ingest_spuf(FIXTURE_DIR, filters=_all_states_filters(), db=spuf_db, version="SPUF.2026.20260115")
    conn = spuf_db.connect()
    try:
        rows = conn.execute(
            "SELECT tier FROM basic_drugs_formulary WHERE formulary_id = 'FORM0001' AND ndc = '00093-7214-01'"
        ).fetchall()
    finally:
        conn.close()
    tiers = [r[0] for r in rows]
    assert 9 not in tiers
    assert tiers == [1]


def test_quantity_limit_and_pa_st_columns_ingested(spuf_db):
    ingest_spuf(FIXTURE_DIR, filters=_all_states_filters(), db=spuf_db, version="SPUF.2026.20260115")
    conn = spuf_db.connect()
    try:
        ql_row = conn.execute(
            "SELECT quantity_limit_yn, quantity_limit_amount, quantity_limit_days "
            "FROM basic_drugs_formulary WHERE formulary_id = 'FORM0001' AND rxcui = '593411'"
        ).fetchone()
        pa_row = conn.execute(
            "SELECT prior_authorization_yn, step_therapy_yn "
            "FROM basic_drugs_formulary WHERE formulary_id = 'FORM0001' AND rxcui = '7646'"
        ).fetchone()
    finally:
        conn.close()
    assert ql_row == (True, 30.0, 30)
    assert pa_row == (True, True)


def test_beneficiary_cost_keeps_all_days_supply_codes_and_coverage_levels(spuf_db):
    """Bug 1: every days_supply CODE (1-4) and coverage_level must survive ingestion,
    not just code 1 / coverage_level 1."""
    ingest_spuf(FIXTURE_DIR, filters=_all_states_filters(), db=spuf_db, version="SPUF.2026.20260115")
    conn = spuf_db.connect()
    try:
        codes = conn.execute(
            "SELECT DISTINCT days_supply_code FROM beneficiary_cost "
            "WHERE plan_key = 'S9999-001' ORDER BY days_supply_code"
        ).fetchall()
        coverage_levels = conn.execute(
            "SELECT DISTINCT coverage_level FROM beneficiary_cost "
            "WHERE plan_key = 'S9999-001' ORDER BY coverage_level"
        ).fetchall()
        ded_row = conn.execute(
            "SELECT ded_applies_yn FROM beneficiary_cost "
            "WHERE plan_key = 'S9999-001' AND tier = 1 LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert [c[0] for c in codes] == [1, 4]
    assert [c[0] for c in coverage_levels] == [0, 1]
    assert ded_row == (False,)


def test_ingest_spuf_from_zip_archive(spuf_db, tmp_path):
    """Regression: zip must stay open while row iterator is consumed."""
    zip_path = tmp_path / "SPUF_2026_20260115.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for path in FIXTURE_DIR.iterdir():
            if path.is_file():
                zf.write(path, arcname=path.name)

    result = ingest_spuf(
        zip_path,
        filters=_all_states_filters(),
        db=spuf_db,
        version="SPUF.2026.20260115",
    )
    assert result["stats"]["plans"] == 4


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

    result = ingest_spuf(
        outer_path,
        filters=_all_states_filters(),
        db=spuf_db,
        version="SPUF.2026.20260115",
    )
    assert result["stats"]["plans"] == 4


def test_ingest_spuf_merge_states_fl_then_tx(spuf_db):
    filters_fl = _all_states_filters(states=["FL"], pdp_region_codes={"FL": "11"})
    result_fl = ingest_spuf(
        FIXTURE_DIR,
        filters=filters_fl,
        db=spuf_db,
        version="SPUF.2026.20260115",
        merge_states=True,
    )
    assert result_fl["stats"]["plans"] == 3
    assert result_fl["stats"]["total_plans"] == 3

    filters_tx = _all_states_filters(states=["TX"], pdp_region_codes={"TX": "22"})
    result_tx = ingest_spuf(
        FIXTURE_DIR,
        filters=filters_tx,
        db=spuf_db,
        version="SPUF.2026.20260115",
        merge_states=True,
    )
    assert result_tx["stats"]["plans"] == 1
    assert result_tx["stats"]["total_plans"] == 4
    assert result_tx["manifest"]["spuf"]["states"] == ["FL", "TX"]

    repo = PlanRepository(db=spuf_db)
    assert len(repo.list_plans(state="FL")) == 3
    assert len(repo.list_plans(state="TX")) == 1


def test_purge_states_with_indexes_and_many_formulary_rows(spuf_db):
    conn = spuf_db.connect()
    try:
        create_tables(conn, drop_existing=True)
        conn.execute(
            "INSERT INTO plans VALUES "
            "('H1290-013', 'H1290', '013', 'FL A', 'MA-PD', 'FL', 0, 2026, 'F1', FALSE)"
        )
        conn.execute(
            "INSERT INTO plans VALUES "
            "('H1290-014', 'H1290', '014', 'FL B', 'MA-PD', 'FL', 0, 2026, 'F2', FALSE)"
        )
        conn.execute(
            "INSERT INTO plans VALUES "
            "('S9999-001', 'S9999', '001', 'TX', 'PDP', 'TX', 0, 2026, 'F3', FALSE)"
        )
        rows = [
            [pk, 1, 1, 1, "preferred_retail", "unknown", None, None, False, "2026-01-01"]
            for pk in ("H1290-013", "H1290-014")
            for _i in range(3000)
        ]
        conn.executemany(
            "INSERT INTO beneficiary_cost VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        create_indexes(conn)
        purged = _purge_states(conn, ["FL"])
        assert purged == 2
        assert conn.execute("SELECT COUNT(*) FROM beneficiary_cost").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM plans WHERE state = 'TX'").fetchone()[0] == 1
    finally:
        conn.close()


def test_ingest_spuf_merge_states_replaces_same_state(spuf_db):
    filters = _all_states_filters(states=["FL"], pdp_region_codes={"FL": "11"})
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
    assert second["stats"]["plans_purged"] == 3
    assert second["stats"]["total_plans"] == 3
    repo = PlanRepository(db=spuf_db)
    assert len(repo.list_plans(state="FL")) == 3
