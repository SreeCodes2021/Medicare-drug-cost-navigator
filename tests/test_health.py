"""Tests for /api/health data freshness fields."""

from fastapi.testclient import TestClient

from medicare_navigator.api.app import app
from tests.spuf_fixture import patch_settings


def test_health_includes_data_freshness(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)

    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "seeded_at" in body
    assert "data_fresh" in body
    assert body["data_fresh"] is True
    assert body["spuf_source_id"] == "cms_spuf_2026_q1"
