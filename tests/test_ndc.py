import pytest

from medicare_navigator.ingestion.ndc import format_ndc_display, ndc_matches, normalize_ndc


def test_normalize_ndc_strips_dashes():
    assert normalize_ndc("00093-7214-01") == "00093721401"


def test_normalize_ndc_rejects_invalid_length():
    with pytest.raises(ValueError):
        normalize_ndc("12345")


def test_format_ndc_display():
    assert format_ndc_display("00093721401") == "00093-7214-01"


def test_ndc_matches_across_formats():
    assert ndc_matches("00093-7214-01", "00093721401")
