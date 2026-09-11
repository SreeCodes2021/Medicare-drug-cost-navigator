from medicare_navigator.ingestion.manifest import merge_manifest, save_manifest
from medicare_navigator.ingestion.preflight import should_skip_spuf_ingest


def test_should_skip_when_version_and_states_match(tmp_path, monkeypatch):
    monkeypatch.setattr("medicare_navigator.ingestion.manifest.settings.data_dir", tmp_path)
    merge_manifest(
        {
            "spuf": {
                "version": "SPUF.2026.20260701",
                "states": ["AR", "TX"],
            }
        }
    )
    skip, reason = should_skip_spuf_ingest(
        version="SPUF.2026.20260701",
        states=["TX", "AR"],
    )
    assert skip is True
    assert "already ingested" in reason


def test_should_not_skip_when_version_differs(tmp_path, monkeypatch):
    monkeypatch.setattr("medicare_navigator.ingestion.manifest.settings.data_dir", tmp_path)
    save_manifest({"spuf": {"version": "SPUF.2026.20260401", "states": ["AR", "TX"]}})
    skip, _ = should_skip_spuf_ingest(
        version="SPUF.2026.20260701",
        states=["AR", "TX"],
    )
    assert skip is False


def test_force_overrides_skip(tmp_path, monkeypatch):
    monkeypatch.setattr("medicare_navigator.ingestion.manifest.settings.data_dir", tmp_path)
    save_manifest({"spuf": {"version": "SPUF.2026.20260701", "states": ["AR", "TX"]}})
    skip, _ = should_skip_spuf_ingest(
        version="SPUF.2026.20260701",
        states=["AR", "TX"],
        force=True,
    )
    assert skip is False


def test_core_only_skips_pharmacy_network(tmp_path, monkeypatch):
    from datetime import date

    from medicare_navigator.config import settings
    from medicare_navigator.ingestion.spuf import ingest_spuf
    from medicare_navigator.storage.connection import DuckDBConnection
    from tests.test_spuf_ingest import FIXTURE_DIR, _fl_filters

    db_path = tmp_path / "core_only.duckdb"
    monkeypatch.setattr(settings, "duckdb_path", db_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db = DuckDBConnection(path=db_path)
    monkeypatch.setattr(
        "medicare_navigator.ingestion.spuf.date",
        type("date", (), {"today": staticmethod(lambda: date(2026, 1, 15))})(),
    )
    ingest_spuf(
        FIXTURE_DIR,
        filters=_fl_filters(),
        db=db,
        version="SPUF.2026.20260115",
        include_pharmacy_network=False,
    )
    conn = db.connect()
    try:
        count = conn.execute("SELECT COUNT(*) FROM pharmacy_network").fetchone()[0]
    finally:
        conn.close()
    assert count == 0
