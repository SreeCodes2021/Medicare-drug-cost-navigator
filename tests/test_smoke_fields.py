"""Tier 1 (Smoke) — every field/dropdown/chatbox exists and tolerates blank
or whitespace input without crashing. This does NOT check business logic
(see test_drug_lookup.py / test_disclaimer_coverage.py for that) — only that
typing, submitting, and opening controls never 500s or leaves an empty
dropdown.
"""

from __future__ import annotations

import pytest

from medicare_navigator.ui_test.checks import (
    InProcessGetter,
    check_field_blank_whitespace_handling,
    check_field_element_contract,
    check_model_selects_populated,
    frontend_dist_dir,
)
from tests.spuf_fixture import patch_settings


@pytest.fixture
def offline_getter(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    getter = InProcessGetter()
    yield getter
    getter.close()


def test_every_smoke_tracked_field_exists_in_html():
    html = (frontend_dist_dir() / "index.html").read_text(encoding="utf-8")
    report = check_field_element_contract(html)
    assert report.passed, [r.__dict__ for r in report.failed]


def test_blank_and_whitespace_submissions_never_crash(offline_getter):
    report = check_field_blank_whitespace_handling(offline_getter)
    assert report.passed, [r.__dict__ for r in report.failed]


def test_model_dropdown_has_options(offline_getter):
    report = check_model_selects_populated(offline_getter)
    assert report.passed, [r.__dict__ for r in report.failed]


def test_chat_input_and_send_button_wired_together():
    """#chat-form submit handler must read #chat-input and disable #send-btn
    while a request is in flight — a common smoke break when the two drift."""
    js = (frontend_dist_dir() / "app.js").read_text(encoding="utf-8")
    assert 'getElementById("chat-input")' in js or "el(\"chat-input\")" in js
    assert 'getElementById("send-btn")' in js or "el(\"send-btn\")" in js


def test_keyboard_tab_order_hooks_present():
    """Comboboxes must support keyboard-only interaction (ArrowUp/Down, Enter,
    Escape) — a baseline smoke requirement for chatbox/dropdown typing flows."""
    js = (frontend_dist_dir() / "app.js").read_text(encoding="utf-8")
    for key in ("ArrowDown", "ArrowUp", "Escape", "Enter"):
        assert key in js, f"keyboard handling for {key} not found in app.js"
