"""Tests for /api/health data freshness fields."""

import pytest
from fastapi.testclient import TestClient

from medicare_navigator.api.app import app
from medicare_navigator.config import settings
from tests.spuf_fixture import patch_settings


def test_health_includes_data_freshness(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "llm_mock_mode", True)

    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["llm_configured"] is True
    assert "seeded_at" in body
    assert "data_fresh" in body
    assert body["data_fresh"] is True
    assert body["spuf_source_id"] == "cms_spuf_2026_q1"


def test_health_fails_loud_without_llm(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "llm_mock_mode", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")

    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["llm_configured"] is False
    assert "error" in body


@pytest.mark.asyncio
async def test_chat_returns_503_without_llm(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "llm_mock_mode", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "metformin tier copay"})
    assert response.status_code == 503
