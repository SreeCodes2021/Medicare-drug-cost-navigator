"""Tests for GET /api/admin/feedback."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from medicare_navigator.api.app import app
from medicare_navigator.config import settings
from tests.spuf_fixture import patch_settings


def _write_feedback_entry(data_dir, *, message, submitted_at, state=None, zip_code=None):
    path = data_dir / "feedback.jsonl"
    entry = {
        "submitted_at": submitted_at,
        "message": message,
        "state": state,
        "zip": zip_code,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def test_admin_feedback_requires_token_when_configured(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "admin_token", "test-secret")

    with TestClient(app) as client:
        assert client.get("/api/admin/feedback").status_code == 403
        assert (
            client.get("/api/admin/feedback", headers={"X-Admin-Token": "wrong"}).status_code
            == 403
        )
        assert (
            client.get(
                "/api/admin/feedback", headers={"X-Admin-Token": "test-secret"}
            ).status_code
            == 200
        )


def test_admin_feedback_404_when_token_unset(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "admin_token", "")

    with TestClient(app) as client:
        assert client.get("/api/admin/feedback").status_code == 404


def test_admin_feedback_empty_before_any_submissions(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "admin_token", "test-secret")

    with TestClient(app) as client:
        response = client.get(
            "/api/admin/feedback", headers={"X-Admin-Token": "test-secret"}
        )
        assert response.status_code == 200
        assert response.json() == {"count": 0, "entries": []}


def test_admin_feedback_lists_submitted_entries_newest_first(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "admin_token", "test-secret")

    with TestClient(app) as client:
        client.post(
            "/api/feedback",
            json={"message": "First submission", "state": "tx", "zip": "75001"},
        )
        client.post("/api/feedback", json={"message": "Second submission"})

        response = client.get(
            "/api/admin/feedback", headers={"X-Admin-Token": "test-secret"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 2
        entries = body["entries"]
        # Newest first.
        assert entries[0]["message"] == "Second submission"
        assert entries[1]["message"] == "First submission"
        assert entries[1]["state"] == "TX"
        assert entries[1]["zip"] == "75001"


def test_admin_feedback_since_until_filter(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "admin_token", "test-secret")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    recent = now
    stale = now - timedelta(days=10)
    _write_feedback_entry(data_dir, message="Recent feedback", submitted_at=recent.isoformat())
    _write_feedback_entry(data_dir, message="Stale feedback", submitted_at=stale.isoformat())

    since = (now - timedelta(days=1)).isoformat()
    until = (now + timedelta(hours=1)).isoformat()

    with TestClient(app) as client:
        response = client.get(
            "/api/admin/feedback",
            params={"since": since, "until": until},
            headers={"X-Admin-Token": "test-secret"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        assert body["entries"][0]["message"] == "Recent feedback"


def test_admin_feedback_rejects_since_after_until(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "admin_token", "test-secret")

    now = datetime.now(timezone.utc)
    since = now.isoformat()
    until = (now - timedelta(hours=1)).isoformat()

    with TestClient(app) as client:
        response = client.get(
            "/api/admin/feedback",
            params={"since": since, "until": until},
            headers={"X-Admin-Token": "test-secret"},
        )
        assert response.status_code == 400


def test_admin_feedback_limit_caps_results(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "admin_token", "test-secret")

    base = datetime.now(timezone.utc).replace(microsecond=0)
    for i in range(5):
        _write_feedback_entry(
            data_dir,
            message=f"Entry {i}",
            submitted_at=(base + timedelta(seconds=i)).isoformat(),
        )

    with TestClient(app) as client:
        response = client.get(
            "/api/admin/feedback",
            params={"limit": 2},
            headers={"X-Admin-Token": "test-secret"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 2
        # Newest first: entries 4 and 3.
        assert body["entries"][0]["message"] == "Entry 4"
        assert body["entries"][1]["message"] == "Entry 3"
