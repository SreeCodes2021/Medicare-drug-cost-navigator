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
                "contract_year": 2026,
            },
        },
    )
    summary = manifest.data_freshness_summary()
    assert summary["spuf_source_id"] == "cms_spuf_2026_q1"
    assert summary["spuf_as_of"] == "2026-01-15"
    assert summary["spuf_version"] == "SPUF.2026.20260115"


def test_parse_spuf_source_id():
    assert manifest.parse_spuf_source_id("cms_spuf_2026_q1") == (2026, 1)
    assert manifest.parse_spuf_source_id("CMS_SPUF_2026_Q2") == (2026, 2)
    assert manifest.parse_spuf_source_id("invalid") is None


def test_list_data_releases_from_manifest(monkeypatch):
    monkeypatch.setattr(
        manifest,
        "load_manifest",
        lambda: {
            "spuf": {
                "source_id": "cms_spuf_2026_q1",
                "as_of": "2026-01-15",
                "version": "SPUF.2026.20260115",
                "contract_year": 2026,
                "quarter": 1,
            }
        },
    )
    releases = manifest.list_data_releases()
    assert len(releases) == 1
    assert releases[0]["id"] == "2026-Q1"
    assert releases[0]["contract_year"] == 2026
    assert releases[0]["quarter"] == 1


def test_get_data_release_uses_explicit_quarter(monkeypatch):
    monkeypatch.setattr(
        manifest,
        "load_manifest",
        lambda: {
            "seeded_at": "2026-08-09",
            "spuf": {
                "source_id": "cms_spuf_2026_q1",
                "contract_year": 2026,
                "quarter": 3,
            },
        },
    )
    release = manifest.get_data_release()
    assert release is not None
    assert release["id"] == "2026-Q3"
    assert release["quarter"] == 3


def test_get_data_release_derives_quarter_from_seeded_at(monkeypatch):
    monkeypatch.setattr(
        manifest,
        "load_manifest",
        lambda: {
            "seeded_at": "2026-08-09",
            "spuf": {"contract_year": 2026},
        },
    )
    release = manifest.get_data_release()
    assert release is not None
    assert release["id"] == "2026-Q3"
    assert release["quarter"] == 3


def test_calendar_quarter_from_date():
    assert manifest.calendar_quarter_from_date(date(2026, 1, 15)) == 1
    assert manifest.calendar_quarter_from_date(date(2026, 8, 9)) == 3
