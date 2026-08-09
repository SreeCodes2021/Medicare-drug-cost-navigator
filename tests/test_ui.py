from pathlib import Path

import pytest

from medicare_navigator.config import settings
from tests.spuf_fixture import patch_settings
from medicare_navigator.ui_test.checks import (
    CHAT_RESPONSE_UI_FIELDS,
    JS_REFERENCED_ELEMENT_IDS,
    InProcessGetter,
    check_api_contract,
    check_app_js_contract,
    check_chat_smoke,
    check_guided_ui_contract,
    check_html_element_contract,
    check_static_files_on_disk,
    check_static_served,
    frontend_dist_dir,
)


@pytest.fixture
def offline_getter(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    getter = InProcessGetter()
    yield getter
    getter.close()


def test_frontend_dist_files_exist():
    report = check_static_files_on_disk()
    assert report.passed, [r.__dict__ for r in report.failed]


def test_index_html_has_required_element_ids():
    html = (frontend_dist_dir() / "index.html").read_text(encoding="utf-8")
    report = check_html_element_contract(html)
    assert report.passed, [r.__dict__ for r in report.failed]


def test_app_js_references_required_elements():
    js = (frontend_dist_dir() / "app.js").read_text(encoding="utf-8")
    report = check_app_js_contract(js)
    assert report.passed, [r.__dict__ for r in report.failed]


def test_guided_estimate_ui_contract():
    dist = frontend_dist_dir()
    report = check_guided_ui_contract(
        (dist / "index.html").read_text(encoding="utf-8"),
        (dist / "app.js").read_text(encoding="utf-8"),
        (dist / "styles.css").read_text(encoding="utf-8"),
    )
    assert report.passed, [r.__dict__ for r in report.failed]


def test_guided_form_is_independent_top_level_tab_with_own_chat():
    html = (frontend_dist_dir() / "index.html").read_text(encoding="utf-8")
    top_tabs = html.index('class="primary-mode-tabs"')
    chat_form = html.index('id="chat-form"')
    guided_panel = html.index('id="mode-guided"')
    guided_chat = html.index('id="guided-chat-messages"')
    assert top_tabs < chat_form
    assert guided_panel < guided_chat
    assert "guided-sheet-backdrop" not in html
    assert "guided-sheet-close" not in html


def test_guided_chat_uses_separate_session_and_five_turn_limit():
    js = (frontend_dist_dir() / "app.js").read_text(encoding="utf-8")
    assert "let guidedSessionId = null" in js
    assert "guidedTurnCount < 5" in js
    assert "guidedTurnCount >= 5" in js
    assert "resetGuidedConversation();" in js
    assert "session_id: guidedSessionId" in js


def test_static_assets_served(offline_getter):
    report = check_static_served(offline_getter)
    assert report.passed, [r.__dict__ for r in report.failed]


def test_ui_api_endpoints(offline_getter):
    report = check_api_contract(offline_getter)
    assert report.passed, [r.__dict__ for r in report.failed]


def test_chat_smoke_offline(offline_getter):
    report = check_chat_smoke(offline_getter)
    assert report.passed, [r.__dict__ for r in report.failed]


def test_guided_smoke_offline(offline_getter):
    from medicare_navigator.ui_test.checks import check_guided_smoke

    report = check_guided_smoke(offline_getter)
    assert report.passed, [r.__dict__ for r in report.failed]


def test_root_html_links_app_js_and_styles():
    html = (frontend_dist_dir() / "index.html").read_text(encoding="utf-8")
    assert 'src="/app.js' in html
    assert 'href="/styles.css' in html


def test_app_js_element_ids_match_html():
    html = (frontend_dist_dir() / "index.html").read_text(encoding="utf-8")
    js = (frontend_dist_dir() / "app.js").read_text(encoding="utf-8")
    html_ids = {eid for eid in JS_REFERENCED_ELEMENT_IDS if f'id="{eid}"' in html}
    js_ids = {eid for eid in JS_REFERENCED_ELEMENT_IDS if eid in js}
    missing_in_js = html_ids - js_ids
    assert not missing_in_js, f"HTML ids not referenced in app.js: {missing_in_js}"


def test_chat_response_fields_documented():
    """Guardrail: if response model drops a field, update CHAT_RESPONSE_UI_FIELDS."""
    from medicare_navigator.models.response import QueryResponse

    model_fields = set(QueryResponse.model_fields)
    for field_name in CHAT_RESPONSE_UI_FIELDS:
        assert field_name in model_fields, f"{field_name} missing from QueryResponse"


def test_guided_state_is_shared_across_guided_submodes_but_not_with_chat():
    """State-carryover contract (frontend/src/app.js):
    - `guidedState` is ONE variable shared by Single/Multiple/Compare so
      switching guided sub-tabs keeps the selected state and its scoped plans.
    - `chatState` is a SEPARATE variable — selecting a state in the Chat tab
      must never leak into the Guided tab's plan scoping, and vice versa.
    A regression here would either silently reset guided plans when switching
    sub-tabs, or leak Chat's location context into Guided (privacy/logic bug).
    """
    js = (frontend_dist_dir() / "app.js").read_text(encoding="utf-8")
    assert "let guidedState = " in js
    assert "let chatState = " in js
    assert "function onGuidedStateChanged(state) {\n  guidedState = state" in js.replace(
        "\r\n", "\n"
    )
    assert "function onChatStateChanged(state) {\n  chatState = state" in js.replace(
        "\r\n", "\n"
    )
    # All three guided submode plan comboboxes read from the one shared guidedState.
    scoped_idx = js.index("function guidedScopedPlans()")
    scoped_block = js[scoped_idx : scoped_idx + 200]
    assert "guidedState" in scoped_block
    instances_idx = js.index("function guidedPlanComboboxInstances()")
    instances_block = js[instances_idx : instances_idx + 200]
    for name in ("primaryPlanCombobox", "mdPlanCombobox", "comparePlanRows"):
        assert name in instances_block, f"{name} must be scoped by the shared guidedState"


def test_guided_drug_and_dosage_fields_reset_per_submode_not_shared():
    """Each guided submode (single/multi/compare) owns its own drug/dosage
    picker instance — selecting metformin in Single must not pre-fill or leak
    into Multi-drug or Compare-plans when the user switches sub-tabs."""
    js = (frontend_dist_dir() / "app.js").read_text(encoding="utf-8")
    assert "const singleDrugPicker = createDrugDosagePicker({" in js
    assert "const compareDrugPicker = createDrugDosagePicker({" in js
    # Multi-drug rows each get their own picker instance (per-row, not shared).
    assert "picker" in js[js.index("drugRows") : js.index("drugRows") + 4000]


def test_guided_submit_buttons_start_disabled_until_required_fields_filled():
    html = (frontend_dist_dir() / "index.html").read_text(encoding="utf-8")
    js = (frontend_dist_dir() / "app.js").read_text(encoding="utf-8")
    for button_id in ("guided-submit", "multidrug-submit", "compareplans-submit"):
        tag = html.split(f'id="{button_id}"', 1)[1].split(">", 1)[0]
        assert " disabled" in tag, f"{button_id} must include disabled in HTML"
    for fn in (
        "isGuidedSingleValid",
        "isGuidedMultiDrugValid",
        "isGuidedComparePlansValid",
        "updateGuidedSubmitButtonState",
    ):
        assert f"function {fn}" in js, f"{fn} must be defined in app.js"
    assert "guidedEstimateInFlight" in js
    assert "onChange: updateGuidedSubmitButtonState" in js
    assert "promptGuidedMandatoryFields" in js


def test_fastapi_mounts_frontend_dist():
    dist = settings.project_root / "frontend" / "dist"
    assert dist.is_dir()
    assert (dist / "index.html").is_file()
