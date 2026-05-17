"""Tests for manifest data freshness helpers."""

from __future__ import annotations

from datetime import date, timedelta

from medicare_navigator.ingestion import manifest


def test_is_data_fresh_when_seeded_today(monkeypatch):
    today = date.today().isoformat()
    monkeypatch.setattr(manifest, "load_manifest", lambda: {"seeded_at": today})
    assert manifest.is_data_fresh() is True
    assert manifest.data_freshness_summary()["data_fresh"] is True


def test_is_data_fresh_when_seeded_yesterday(monkeypatch):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    monkeypatch.setattr(manifest, "load_manifest", lambda: {"seeded_at": yesterday})
    assert manifest.is_data_fresh() is True


def test_is_stale_when_seeded_two_days_ago(monkeypatch):
    old = (date.today() - timedelta(days=2)).isoformat()
    monkeypatch.setattr(manifest, "load_manifest", lambda: {"seeded_at": old})
    assert manifest.is_data_fresh() is False
    assert manifest.data_freshness_summary()["data_fresh"] is False


def test_is_stale_when_no_manifest(monkeypatch):
    monkeypatch.setattr(manifest, "load_manifest", lambda: {})
    assert manifest.get_seeded_at() is None
    assert manifest.is_data_fresh() is False


def test_freshness_summary_includes_spuf_fields(monkeypatch):
    monkeypatch.setattr(
        manifest,
        "load_manifest",
        lambda: {
            "seeded_at": date.today().isoformat(),
            "spuf": {
                "source_id": "cms_spuf_2026_q1",
                "as_of": "2026-01-15",
                "version": "SPUF.2026.20260115",
            },
        },
    )
    summary = manifest.data_freshness_summary()
    assert summary["spuf_source_id"] == "cms_spuf_2026_q1"
    assert summary["spuf_as_of"] == "2026-01-15"
    assert summary["spuf_version"] == "SPUF.2026.20260115"


def test_policy_corpus_manifest_readable(tmp_path, monkeypatch):
    from medicare_navigator.ingestion.policy_corpus import ingest_policy_corpus
    from medicare_navigator.storage.connection import DuckDBConnection

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(manifest.settings, "data_dir", data_dir)
    ingest_policy_corpus(
        db=DuckDBConnection(path=data_dir / "navigator.duckdb"),
        chroma_path=data_dir / "chroma",
    )
    assert manifest.get_as_of("policy_corpus") == "2026-01-15"
