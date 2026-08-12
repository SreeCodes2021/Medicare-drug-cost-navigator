from pathlib import Path
import zipfile
from datetime import date
from io import BytesIO

import pytest

from medicare_navigator.config import settings
from medicare_navigator.ingestion.schema import create_indexes, create_tables
from medicare_navigator.ingestion.spuf import (
    IngestFilters,
    _extract_cost_shares,
    _purge_states,
    _pricing_insert_row,
    ingest_spuf,
)
from medicare_navigator.storage.connection import DuckDBConnection
from medicare_navigator.storage.repository import InsulinBeneficiaryCostRepository, PlanRepository

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "spuf"


@pytest.fixture
def spuf_db(tmp_path, monkeypatch):
    db_path = tmp_path / "spuf_test.duckdb"
    monkeypatch.setattr(settings, "duckdb_path", db_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return DuckDBConnection(path=db_path)


def _fl_filters(**overrides) -> IngestFilters:
    defaults = dict(
        contract_year=2026,
        states=["FL"],
        pdp_region_codes={"FL": "11"},
        plan_type_prefixes=["S", "H"],
    )
    defaults.update(overrides)
    return IngestFilters(**defaults)


def test_ingest_spuf_fixture_loads_fl_plans(spuf_db, monkeypatch):
    monkeypatch.setattr(
        "medicare_navigator.ingestion.spuf.date",
        type("date", (), {"today": staticmethod(lambda: date(2026, 1, 15))})(),
    )
    result = ingest_spuf(
        FIXTURE_DIR,
        filters=_fl_filters(),
        db=spuf_db,
        version="SPUF.2026.20260115",
    )

    # 5 plans total: S9999-001 (FL), H8888-001 (FL), H5427-060 (FL), S9999-003 (FL, suppressed),
    # S9999-004 (FL, partial pharmacy-channel coverage)
    assert result["stats"]["plans"] == 5
    assert result["stats"]["formulary_rows"] >= 3
    assert result["source_id"] == "cms_spuf_2026_q1"
    assert result["manifest"]["spuf"]["quarter"] == 1

    repo = PlanRepository(db=spuf_db)
    fl_plans = repo.list_plans(state="FL")
    assert len(fl_plans) == 5
    assert any(p["plan_key"] == "S9999-001" for p in fl_plans)
    assert any(p["plan_key"] == "H8888-001" for p in fl_plans)
    assert any(p["plan_key"] == "S9999-003" for p in fl_plans)


def test_ingest_spuf_uses_ingest_date_quarter(spuf_db, monkeypatch):
    monkeypatch.setattr(
        "medicare_navigator.ingestion.spuf.date",
        type("date", (), {"today": staticmethod(lambda: date(2026, 5, 10))})(),
    )
    result = ingest_spuf(
        FIXTURE_DIR,
        filters=_fl_filters(),
        db=spuf_db,
        version="SPUF.2026.20260510",
    )
    assert result["source_id"] == "cms_spuf_2026_q2"
    assert result["manifest"]["spuf"]["quarter"] == 2


def test_suppressed_plan_is_ingested_not_filtered(spuf_db):
    """Bug 6: suppressed plans must still be selectable, not silently dropped at ingest."""
    ingest_spuf(FIXTURE_DIR, filters=_fl_filters(), db=spuf_db, version="SPUF.2026.20260115")
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
    ingest_spuf(FIXTURE_DIR, filters=_fl_filters(), db=spuf_db, version="SPUF.2026.20260115")
    conn = spuf_db.connect()
    try:
        rows = conn.execute(
            "SELECT tier FROM basic_drugs_formulary WHERE formulary_id = 'FORM0001' AND ndc = '00093-7214-01'"
        ).fetchall()
    finally:
        conn.close()
    tiers = [r[0] for r in rows]
    assert 9 not in tiers
    assert all(t == 1 for t in tiers)


def test_quantity_limit_and_pa_st_columns_ingested(spuf_db):
    ingest_spuf(FIXTURE_DIR, filters=_fl_filters(), db=spuf_db, version="SPUF.2026.20260115")
    conn = spuf_db.connect()
    try:
        ql_row = conn.execute(
            "SELECT quantity_limit_yn, quantity_limit_amount, quantity_limit_days "
            "FROM basic_drugs_formulary WHERE formulary_id = 'FORM0001' AND rxcui = '638596'"
        ).fetchone()
        pa_row = conn.execute(
            "SELECT prior_authorization_yn, step_therapy_yn "
            "FROM basic_drugs_formulary WHERE formulary_id = 'FORM0001' AND rxcui = '198051'"
        ).fetchone()
    finally:
        conn.close()
    assert ql_row == (True, 30.0, 30)
    assert pa_row == (True, True)


def test_beneficiary_cost_keeps_all_days_supply_codes_and_coverage_levels(spuf_db):
    """Bug 1: every days_supply CODE (1-4) and coverage_level must survive ingestion,
    not just code 1 / coverage_level 1."""
    ingest_spuf(FIXTURE_DIR, filters=_fl_filters(), db=spuf_db, version="SPUF.2026.20260115")
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
    assert [c[0] for c in coverage_levels] == [0, 1, 3]
    assert ded_row == (False,)


def test_insulin_beneficiary_cost_ingested_separately_from_general_file(spuf_db):
    """Hint-collision regression: the real CMS zip member for the insulin file is named
    "insulin beneficiary cost file ...", which contains the general file's own
    BENEFICIARY_COST_FILE_HINTS substring ("beneficiary cost"). File discovery must not
    let the two cross-contaminate in either direction."""
    ingest_spuf(FIXTURE_DIR, filters=_fl_filters(), db=spuf_db, version="SPUF.2026.20260115")
    conn = spuf_db.connect()
    try:
        insulin_rows = conn.execute(
            "SELECT days_supply_code, pharmacy_channel, copay FROM insulin_beneficiary_cost "
            "WHERE plan_key = 'S9999-001' AND tier = 3"
        ).fetchall()
        general_tier3_types = conn.execute(
            "SELECT DISTINCT cost_type FROM beneficiary_cost WHERE plan_key = 'S9999-001' AND tier = 3"
        ).fetchall()
    finally:
        conn.close()
    # 3 fixture rows (30/60/90-day) x 4 channels, all populated in the fixture.
    assert len(insulin_rows) == 12
    thirty_day = {channel: copay for code, channel, copay in insulin_rows if code == 1}
    assert thirty_day == {
        "preferred_retail": 35.0,
        "standard_retail": 35.0,
        "preferred_mail": 30.0,
        "standard_mail": 35.0,
    }
    # The general file's own tier-3 rows (an unrelated drug) are untouched/uninflated by
    # the insulin file — still exactly the copay-type rows the general fixture defines.
    assert general_tier3_types == [("copay",)]


def test_insulin_beneficiary_cost_repository_scaling_and_narrow_fallback(spuf_db):
    """30/60/90-day scaling via the repository, plus the narrow insulin_out_of_scope
    fallback case: H8888-001 has lantus on its formulary (tier 2) but no insulin
    cost-share row at all — has_any must report False, not silently return $0."""
    ingest_spuf(FIXTURE_DIR, filters=_fl_filters(), db=spuf_db, version="SPUF.2026.20260115")
    repo = InsulinBeneficiaryCostRepository(db=spuf_db)

    assert repo.get_cost_share("S9999-001", 3, 1, pharmacy_channel="preferred_retail") == 35.0
    assert repo.get_cost_share("S9999-001", 3, 4, pharmacy_channel="preferred_retail") == 70.0
    assert repo.get_cost_share("S9999-001", 3, 2, pharmacy_channel="preferred_mail") == 90.0
    assert repo.has_any("S9999-001", 3, 1) is True

    assert repo.has_any("H8888-001", 2, 1) is False
    assert repo.get_cost_share("H8888-001", 2, 1) is None

    # Defined-standard NULL-tier fallback: humalog tier 1 on S9999-004 has no exact-tier
    # row but does have a CMS "." sentinel row stored as tier IS NULL.
    assert repo.get_cost_share("S9999-004", 1, 1, pharmacy_channel="preferred_retail") == 12.0


def test_insulin_null_tier_sentinel_ingested(spuf_db):
    """CMS '.' tier sentinel in the insulin file is stored as NULL and queryable."""
    ingest_spuf(FIXTURE_DIR, filters=_fl_filters(), db=spuf_db, version="SPUF.2026.20260115")
    conn = spuf_db.connect()
    try:
        row = conn.execute(
            "SELECT tier, copay FROM insulin_beneficiary_cost "
            "WHERE plan_key = 'S9999-004' AND days_supply_code = 1 "
            "AND pharmacy_channel = 'preferred_retail'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] is None
    assert row[1] == 12.0


def test_copay_cost_max_placeholder_zero_is_dropped_not_treated_as_ceiling():
    """CMS fills COST_MAX_AMT_* with literal '0' for flat-copay rows (COST_TYPE=1); it only
    bounds a real dollar range for coinsurance rows (COST_TYPE=2). Treating that placeholder
    zero as an actual $0 payment ceiling would zero out real copays (regression)."""
    row = {
        "TIER": "1",
        "COVERAGE_LEVEL": "0",
        "DAYS_SUPPLY": "1",
        "DED_APPLIES_YN": "N",
        "COST_TYPE_PREF": "1",
        "COST_AMT_PREF": "5",
        "COST_MAX_AMT_PREF": "0",
        "COST_TYPE_NONPREF": "2",
        "COST_AMT_NONPREF": "0.25",
        "COST_MAX_AMT_NONPREF": "45",
        "COST_TYPE_MAIL_PREF": "0",
        "COST_AMT_MAIL_PREF": "0",
        "COST_MAX_AMT_MAIL_PREF": "0",
        "COST_TYPE_MAIL_NONPREF": "0",
        "COST_AMT_MAIL_NONPREF": "0",
        "COST_MAX_AMT_NONPREF ": "0",
    }
    shares = {s["pharmacy_channel"]: s for s in _extract_cost_shares(row)}
    assert shares["preferred_retail"]["cost_type"] == "copay"
    assert shares["preferred_retail"]["copay"] == 5.0
    assert shares["preferred_retail"]["cost_max"] is None
    assert shares["standard_retail"]["cost_type"] == "coinsurance"
    assert shares["standard_retail"]["cost_max"] == 45.0


def test_ingest_spuf_from_zip_archive(spuf_db, tmp_path):
    """Regression: zip must stay open while row iterator is consumed."""
    zip_path = tmp_path / "SPUF_2026_20260115.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for path in FIXTURE_DIR.iterdir():
            if path.is_file():
                zf.write(path, arcname=path.name)

    result = ingest_spuf(
        zip_path,
        filters=_fl_filters(),
        db=spuf_db,
        version="SPUF.2026.20260115",
    )
    assert result["stats"]["plans"] == 5


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
        filters=_fl_filters(),
        db=spuf_db,
        version="SPUF.2026.20260115",
    )
    assert result["stats"]["plans"] == 5


def test_ingest_spuf_merge_states_fl_only(spuf_db):
    filters_fl = _fl_filters()
    result_fl = ingest_spuf(
        FIXTURE_DIR,
        filters=filters_fl,
        db=spuf_db,
        version="SPUF.2026.20260115",
        merge_states=True,
    )
    assert result_fl["stats"]["plans"] == 5
    assert result_fl["stats"]["total_plans"] == 5
    assert result_fl["manifest"]["spuf"]["states"] == ["FL"]

    repo = PlanRepository(db=spuf_db)
    assert len(repo.list_plans(state="FL")) == 5


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
            "('S9999-001', 'S9999', '001', 'CA PDP', 'PDP', 'CA', 0, 2026, 'F3', FALSE)"
        )
        rows = [
            [pk, 1, 1, 1, "preferred_retail", "unknown", None, None, None, False, "2026-01-01"]
            for pk in ("H1290-013", "H1290-014")
            for _i in range(3000)
        ]
        conn.executemany(
            "INSERT INTO beneficiary_cost VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        create_indexes(conn)
        purged = _purge_states(conn, ["FL"])
        assert purged == 2
        assert conn.execute("SELECT COUNT(*) FROM beneficiary_cost").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM plans WHERE state = 'CA'").fetchone()[0] == 1
    finally:
        conn.close()


def test_ingest_spuf_merge_states_refreshes_formulary_for_replaced_state(spuf_db):
    """Re-merging a state must replace basic_drugs_formulary rows for that state's
    formulary IDs so fixture anchors like humalog tier 1 are not left stale."""
    filters = _fl_filters(states=["FL"], pdp_region_codes={"FL": "11"})
    ingest_spuf(
        FIXTURE_DIR,
        filters=filters,
        db=spuf_db,
        version="SPUF.2026.20260115",
        merge_states=True,
    )
    conn = spuf_db.connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM basic_drugs_formulary WHERE formulary_id='FORM0001' AND rxcui='135805'"
        ).fetchone()[0] == 1
        conn.execute(
            "DELETE FROM basic_drugs_formulary WHERE formulary_id='FORM0001' AND rxcui='135805'"
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM basic_drugs_formulary WHERE formulary_id='FORM0001' AND rxcui='135805'"
        ).fetchone()[0] == 0
    finally:
        conn.close()

    ingest_spuf(
        FIXTURE_DIR,
        filters=filters,
        db=spuf_db,
        version="SPUF.2026.20260115",
        merge_states=True,
    )
    conn = spuf_db.connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM basic_drugs_formulary WHERE formulary_id='FORM0001' AND rxcui='135805'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM insulin_beneficiary_cost WHERE plan_key='S9999-001' AND tier=1"
        ).fetchone()[0] == 4
    finally:
        conn.close()


def test_ingest_spuf_merge_states_replaces_same_state(spuf_db):
    filters = _fl_filters(states=["FL"], pdp_region_codes={"FL": "11"})
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
    assert second["stats"]["plans_purged"] == 5
    assert second["stats"]["total_plans"] == 5
    repo = PlanRepository(db=spuf_db)
    assert len(repo.list_plans(state="FL")) == 5


def test_pricing_insert_row_preserves_literal_zero_days_supply():
    """DAYS_SUPPLY='0' is falsy in Python; a plain `or 30` default would silently mislabel
    this row as a 30-day price instead of preserving the CMS-published value of 0."""
    plans = {"S9999-001": {}}
    row = {
        "CONTRACT_ID": "S9999",
        "PLAN_ID": "001",
        "NDC": "00093721401",
        "DAYS_SUPPLY": "0",
        "UNIT_COST": "0.15",
    }
    insert_row = _pricing_insert_row(row, plans)
    assert insert_row is not None
    assert insert_row[2] == 0


def test_pricing_insert_row_defaults_missing_days_supply_to_30():
    plans = {"S9999-001": {}}
    row = {
        "CONTRACT_ID": "S9999",
        "PLAN_ID": "001",
        "NDC": "00093721401",
        "DAYS_SUPPLY": "",
        "UNIT_COST": "0.15",
    }
    insert_row = _pricing_insert_row(row, plans)
    assert insert_row is not None
    assert insert_row[2] == 30


def _catalog_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "ingest_filters.yaml"
    path.write_text(
        """
contract_year: 2026
states:
  - AR
  - TX
pdp_region_codes:
  AR: "19"
  TX: "22"
  FL: "11"
plan_type_prefixes:
  - S
  - H
""".strip(),
        encoding="utf-8",
    )
    return path


def test_ingest_filters_resolve_uses_yaml_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("INGEST_STATES", raising=False)
    monkeypatch.setattr(settings, "ingest_states", "")
    filters = IngestFilters.resolve(path=_catalog_yaml(tmp_path))
    assert filters.states == ["AR", "TX"]
    assert filters.pdp_region_codes["FL"] == "11"


def test_ingest_filters_resolve_env_intersection(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ingest_states", "AR,FL,ZZ")
    filters = IngestFilters.resolve(path=_catalog_yaml(tmp_path))
    assert filters.states == ["AR", "FL"]


def test_ingest_filters_resolve_cli_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ingest_states", "AR,FL")
    filters = IngestFilters.resolve(path=_catalog_yaml(tmp_path), states_override="TX")
    assert filters.states == ["TX"]


def test_ingest_filters_resolve_empty_selection_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ingest_states", "ZZ")
    with pytest.raises(ValueError, match="No ingest states selected"):
        IngestFilters.resolve(path=_catalog_yaml(tmp_path))
