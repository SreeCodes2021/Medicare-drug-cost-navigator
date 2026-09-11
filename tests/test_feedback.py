"""Tests for POST /api/feedback."""

import json

from fastapi.testclient import TestClient

from medicare_navigator.api.app import app
from medicare_navigator.config import settings
from tests.spuf_fixture import patch_settings


def test_feedback_submission(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)

    client = TestClient(app)
    response = client.post(
        "/api/feedback",
        json={
            "message": "The pharmacy lookup was confusing.",
            "state": "tx",
            "zip": "75001",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["submitted_at"]

    lines = (data_dir / "feedback.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["message"] == "The pharmacy lookup was confusing."
    assert entry["state"] == "TX"
    assert entry["zip"] == "75001"


def test_feedback_requires_message(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)

    client = TestClient(app)
    response = client.post("/api/feedback", json={"message": "   "})
    assert response.status_code == 400


def test_feedback_validates_zip(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)

    client = TestClient(app)
    response = client.post(
        "/api/feedback",
        json={"message": "Helpful app", "zip": "123"},
    )
    assert response.status_code == 400
