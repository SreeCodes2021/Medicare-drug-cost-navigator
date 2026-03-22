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


def test_static_assets_served(offline_getter):
    report = check_static_served(offline_getter)
    assert report.passed, [r.__dict__ for r in report.failed]


def test_ui_api_endpoints(offline_getter):
    report = check_api_contract(offline_getter)
    assert report.passed, [r.__dict__ for r in report.failed]


def test_chat_smoke_offline(offline_getter):
    report = check_chat_smoke(offline_getter)
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


def test_fastapi_mounts_frontend_dist():
    dist = settings.project_root / "frontend" / "dist"
    assert dist.is_dir()
    assert (dist / "index.html").is_file()
