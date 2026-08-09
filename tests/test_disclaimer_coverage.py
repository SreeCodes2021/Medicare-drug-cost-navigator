"""Sweep every chat status and API surface to lock in "disclaimer everywhere".

Backing mechanism (see src/medicare_navigator/agent/navigator.py and
guardrails/citations.py):
- `QueryResponse.disclaimer` is set to `settings.disclaimer_text` on every path
  (ok, needs_clarification, not_found, limit_reached) — never left blank.
- For the "ok" LLM path, `apply_guardrails` force-appends the disclaimer text
  into `explanation` itself if the model dropped it.
- `PlanComparisonApiResponse.disclaimer` carries a comparison-specific caveat
  (premiums not included / not a switch recommendation) on every compare-plans
  response regardless of per-item status.
- The static `#disclaimer-banner` (GET /api/disclaimer) is independent of any
  of the above and must never be empty.

This file does not re-invent the mechanism; it is a regression contract so a
future change can't silently drop the disclaimer for one status or surface.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from medicare_navigator.api.app import app
from medicare_navigator.config import settings
from tests.spuf_fixture import PLAN_FL_MAPD, PLAN_FL_PDP

client = TestClient(app)


def _disclaimer_text_nonempty() -> str:
    text = settings.disclaimer_text
    assert text and text.strip(), "settings.disclaimer_text must not be empty"
    return text


@pytest.mark.parametrize(
    "message,filters",
    [
        # ok
        ("What's the cost for metformin 500mg on plan S9999-001?", None),
        # needs_clarification (no drug named)
        ("What's my copay?", None),
        # not_found (nonsense drug)
        ("What's the cost for zzzznotarealdrugzzzz on plan S9999-001?", None),
    ],
)
def test_chat_response_always_carries_disclaimer_field(message, filters):
    disclaimer_text = _disclaimer_text_nonempty()
    payload = {"message": message}
    if filters:
        payload["filters"] = filters
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    inner = response.json()["response"]
    assert inner["disclaimer"], f"status={inner.get('status')} missing disclaimer field"
    assert inner["disclaimer"] == disclaimer_text


def test_chat_ok_explanation_contains_disclaimer_text_inline():
    """The 'ok' path force-appends disclaimer text into the explanation itself
    (guardrails/citations.py) — not just the separate `disclaimer` field —
    because the frontend does not render `response.disclaimer` for chat/guided,
    it relies on the explanation text plus the static banner."""
    disclaimer_text = _disclaimer_text_nonempty()
    response = client.post(
        "/api/chat",
        json={"message": "What's the cost for metformin 500mg on plan S9999-001?"},
    )
    assert response.status_code == 200
    inner = response.json()["response"]
    if inner["status"] == "ok":
        assert disclaimer_text in inner["explanation"]


def test_chat_off_topic_explanation_contains_disclaimer_text_inline():
    """System off-topic early-return must inline the disclaimer (navigator.py), not only
    the separate disclaimer field."""
    disclaimer_text = _disclaimer_text_nonempty()
    response = client.post(
        "/api/chat",
        json={"message": "What's the weather in Miami today?"},
    )
    assert response.status_code == 200
    inner = response.json()["response"]
    assert inner["status"] == "ok"
    assert disclaimer_text in inner["explanation"]


def test_limit_reached_status_still_carries_disclaimer():
    """After 5 turns in a session, status becomes limit_reached — disclaimer
    must still be present (agent/navigator.py returns it explicitly on this
    early-return path)."""
    disclaimer_text = _disclaimer_text_nonempty()
    session_id = None
    last = None
    for _ in range(6):
        payload = {"message": "What's the cost for metformin 500mg on plan S9999-001?"}
        if session_id:
            payload["session_id"] = session_id
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 200
        data = response.json()
        session_id = data["session_id"]
        last = data["response"]
    assert last["status"] == "limit_reached"
    assert last["disclaimer"] == disclaimer_text


def test_compare_plans_response_always_has_disclaimer(spuf_db):
    response = client.post(
        "/api/compare-plans",
        json={
            "drug": "metformin",
            "dosage": "500mg",
            "plan_ids": [PLAN_FL_PDP, PLAN_FL_MAPD],
            "days_supply": 30,
            "ytd_oop_spend": 0,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["disclaimer"].strip()
    assert "premium" in body["disclaimer"].lower()


def test_disclaimer_banner_endpoint_never_empty():
    response = client.get("/api/disclaimer")
    assert response.status_code == 200
    assert response.json()["text"].strip()


def test_disclaimer_banner_present_regardless_of_active_tab():
    """The banner markup is a single static element outside both the Chat and
    Guided tab panels, so switching tabs client-side can never hide it."""
    from medicare_navigator.ui_test.checks import frontend_dist_dir

    html = (frontend_dist_dir() / "index.html").read_text(encoding="utf-8")
    banner_idx = html.index('id="disclaimer-banner"')
    chat_panel_idx = html.index('id="mode-chat"')
    guided_panel_idx = html.index('id="mode-guided"')
    assert banner_idx < chat_panel_idx
    assert banner_idx < guided_panel_idx
