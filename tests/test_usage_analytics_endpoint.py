"""End-to-end: /api/chat records usage, a manual flush persists it, and
/api/admin/usage reads it back gated by ADMIN_TOKEN."""

from __future__ import annotations

from fastapi.testclient import TestClient

from medicare_navigator.analytics.collector import collector
from medicare_navigator.analytics.flush import flush_now
from medicare_navigator.api.app import app
from medicare_navigator.config import settings
from tests.spuf_fixture import PLAN_FL_PDP, patch_settings


def test_chat_request_flushes_to_usage_hourly(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "llm_mock_mode", True)
    monkeypatch.setattr(settings, "admin_token", "test-secret")
    collector.drain()  # clear any state from other tests sharing the singleton

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"message": "What's the cost for metformin 500mg?"},
        )
        assert response.status_code == 200

        flush_now()

        usage = client.get("/api/admin/usage", headers={"X-Admin-Token": "test-secret"})
        assert usage.status_code == 200
        rows = usage.json()["rows"]
        # One row for the new-session counter (mode/model unknown at session-creation
        # time) and one for the request itself (mode="chat", a resolved model).
        assert len(rows) == 2
        request_row = next(r for r in rows if r["requests_total"] >= 1)
        assert request_row["region"] == "unknown"
        assert request_row["mode"] == "chat"
        assert request_row["model"] != "unknown"


def test_chat_request_falls_back_to_plan_state_when_region_omitted(tmp_path, monkeypatch):
    """A user who types a plan ID directly (skipping the state picker) should
    still get a real region — resolved server-side from the plan's own state."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "llm_mock_mode", True)
    monkeypatch.setattr(settings, "admin_token", "test-secret")
    collector.drain()

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={
                "message": "What's the cost for metformin 500mg?",
                "filters": {"plan_id": PLAN_FL_PDP},
            },
        )
        assert response.status_code == 200

        flush_now()

        usage = client.get("/api/admin/usage", headers={"X-Admin-Token": "test-secret"})
        rows = usage.json()["rows"]
        by_region = {row["region"]: row for row in rows}
        assert by_region["FL"]["requests_total"] == 1


def test_chat_request_falls_back_to_plan_state_from_message_text(tmp_path, monkeypatch):
    """A plan ID typed directly in the chat message (no plan-picker, no filters)
    should still resolve a region — this is the gap the plan-picker fallback
    alone doesn't cover, since filters.plan_id is empty in that case."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "llm_mock_mode", True)
    monkeypatch.setattr(settings, "admin_token", "test-secret")
    collector.drain()

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"message": f"What's the cost for metformin 500mg on plan {PLAN_FL_PDP}?"},
        )
        assert response.status_code == 200

        flush_now()

        usage = client.get("/api/admin/usage", headers={"X-Admin-Token": "test-secret"})
        rows = usage.json()["rows"]
        by_region = {row["region"]: row for row in rows}
        assert by_region["FL"]["requests_total"] == 1


def test_chat_request_with_region_buckets_by_state(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "llm_mock_mode", True)
    monkeypatch.setattr(settings, "admin_token", "test-secret")
    collector.drain()

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={
                "message": f"What's the cost for metformin 500mg on plan {PLAN_FL_PDP}?",
                "region": "fl",  # lowercase in, normalized to FL
            },
        )
        assert response.status_code == 200

        flush_now()

        usage = client.get("/api/admin/usage", headers={"X-Admin-Token": "test-secret"})
        rows = usage.json()["rows"]
        # New-session tracking (session/manager.py) doesn't yet know the caller's
        # region, so it's bucketed separately under "unknown" — only the
        # request/prompt-shape counters land in the region-specific row.
        by_region = {row["region"]: row for row in rows}
        assert by_region["FL"]["requests_total"] == 1
        assert by_region["unknown"]["sessions_new"] == 1


def test_chat_request_with_junk_region_and_no_plan_falls_back_to_unknown(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "llm_mock_mode", True)
    monkeypatch.setattr(settings, "admin_token", "test-secret")
    collector.drain()

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={
                "message": "What's the cost for metformin 500mg?",
                "region": "not-a-state; DROP TABLE usage_hourly;",
            },
        )
        assert response.status_code == 200

        flush_now()

        usage = client.get("/api/admin/usage", headers={"X-Admin-Token": "test-secret"})
        rows = usage.json()["rows"]
        # Session row (mode/model unknown) + request row (mode="chat"), both region="unknown".
        assert len(rows) == 2
        assert all(r["region"] == "unknown" for r in rows)


def test_chat_request_with_junk_region_still_prefers_plan_id_in_message(tmp_path, monkeypatch):
    """A garbage region field shouldn't block the message-text plan-ID fallback."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "llm_mock_mode", True)
    monkeypatch.setattr(settings, "admin_token", "test-secret")
    collector.drain()

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={
                "message": f"What's the cost for metformin 500mg on plan {PLAN_FL_PDP}?",
                "region": "not-a-state",
            },
        )
        assert response.status_code == 200

        flush_now()

        usage = client.get("/api/admin/usage", headers={"X-Admin-Token": "test-secret"})
        rows = usage.json()["rows"]
        by_region = {row["region"]: row for row in rows}
        assert by_region["FL"]["requests_total"] == 1


def test_chat_request_records_mode_and_token_cost_totals(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "llm_mock_mode", True)
    monkeypatch.setattr(settings, "admin_token", "test-secret")
    collector.drain()

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={
                "message": "What's the cost for metformin 500mg?",
                "mode": "guided_single",
            },
        )
        assert response.status_code == 200

        flush_now()

        usage = client.get("/api/admin/usage", headers={"X-Admin-Token": "test-secret"})
        rows = usage.json()["rows"]
        request_row = next(r for r in rows if r["requests_total"] >= 1)
        assert request_row["mode"] == "guided_single"
        assert request_row["model"] != "unknown"
        assert request_row["requests_ok"] == 1
        assert request_row["tokens_in_sum"] > 0
        assert request_row["tokens_out_sum"] > 0
        assert request_row["cost_usd_sum"] > 0


def test_chat_request_with_unrecognized_mode_falls_back_to_chat(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "llm_mock_mode", True)
    monkeypatch.setattr(settings, "admin_token", "test-secret")
    collector.drain()

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"message": "What's the cost for metformin 500mg?", "mode": "bogus"},
        )
        assert response.status_code == 200

        flush_now()

        usage = client.get("/api/admin/usage", headers={"X-Admin-Token": "test-secret"})
        rows = usage.json()["rows"]
        request_row = next(r for r in rows if r["requests_total"] >= 1)
        assert request_row["mode"] == "chat"


def test_admin_usage_requires_token_when_configured(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "admin_token", "test-secret")

    with TestClient(app) as client:
        assert client.get("/api/admin/usage").status_code == 403
        assert (
            client.get("/api/admin/usage", headers={"X-Admin-Token": "wrong"}).status_code == 403
        )
        assert (
            client.get(
                "/api/admin/usage", headers={"X-Admin-Token": "test-secret"}
            ).status_code
            == 200
        )


def test_admin_usage_404_when_token_unset(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    monkeypatch.setattr(settings, "admin_token", "")

    with TestClient(app) as client:
        assert client.get("/api/admin/usage").status_code == 404
