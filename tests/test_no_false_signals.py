"""Regression: UI must not show misleading pre-estimate signals (formulary picker labels)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "frontend" / "src" / "app.js"

FORBIDDEN_PICKER_STRINGS = (
    '"On formulary"',
    '"Not on formulary"',
    "picker-meta--on-formulary",
    "picker-meta--off-formulary",
)

FORBIDDEN_COPY_STRINGS = (
    "shown in the picker",
)


def test_app_js_does_not_show_formulary_labels_in_picker():
    text = APP_JS.read_text(encoding="utf-8")
    for forbidden in FORBIDDEN_PICKER_STRINGS:
        assert forbidden not in text, (
            f"frontend/src/app.js must not contain {forbidden!r} — "
            "picker must not imply formulary coverage before an estimate runs"
        )


def test_app_js_does_not_reference_picker_for_coverage():
    text = APP_JS.read_text(encoding="utf-8")
    for forbidden in FORBIDDEN_COPY_STRINGS:
        assert forbidden not in text, (
            f"frontend/src/app.js must not contain {forbidden!r} — "
            "estimate results are the source of truth for coverage"
        )


def test_normalize_combobox_option_does_not_map_on_formulary():
    text = APP_JS.read_text(encoding="utf-8")
    start = text.find("function normalizeComboboxOption")
    assert start != -1
    end = text.find("\nfunction ", start + 1)
    block = text[start:end] if end != -1 else text[start:]
    assert "on_formulary" not in block, (
        "normalizeComboboxOption must not read on_formulary — "
        "picker options are name/dosage only"
    )
