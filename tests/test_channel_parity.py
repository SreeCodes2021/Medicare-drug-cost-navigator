"""Tests for channel parity helpers."""

from medicare_navigator.guardrails.channel_parity import (
    channel_coverage_note,
    channel_wording_for_channels,
    prose_channel_overclaim_warnings,
    summarize_channel_coverage,
    summarize_channels_dict,
)


def test_summarize_channels_dict_partial_coverage():
    channels = {
        "preferred_retail": {"cost_low": 5.0, "cost_high": 5.0},
        "standard_retail": {"cost_low": 13.0, "cost_high": 13.0},
        "preferred_mail": {"cost_low": None, "cost_high": None},
        "standard_mail": {"cost_low": None, "cost_high": None},
    }
    summary = summarize_channels_dict(channels)
    assert summary["priced_channels"] == ["preferred_retail", "standard_retail"]
    assert summary["missing_channels"] == ["preferred_mail", "standard_mail"]


def test_channel_wording_single_priced_channel():
    channels = {
        "preferred_retail": {"cost_low": None},
        "standard_retail": {"cost_low": 0.0, "cost_high": 0.0},
        "preferred_mail": {"cost_low": None},
        "standard_mail": {"cost_low": None},
    }
    note = channel_wording_for_channels(channels)
    assert "Standard retail only" in note
    assert "no matching estimate" in note


def test_channel_coverage_note_lists_missing_channels():
    coverage = summarize_channel_coverage(
        [
            {
                "plan_key": "S9999-004",
                "channels": {
                    "preferred_retail": {"cost_low": None},
                    "standard_retail": {"cost_low": 5.0, "cost_high": 5.0},
                    "preferred_mail": {"cost_low": None},
                    "standard_mail": {"cost_low": None},
                },
            }
        ]
    )
    note = channel_coverage_note(coverage)
    assert note is not None
    assert "S9999-004" in note
    assert "Standard retail" in note


def test_prose_channel_overclaim_warnings():
    coverage = summarize_channel_coverage(
        [
            {
                "plan_key": "H2802-063",
                "channels": {
                    "preferred_retail": {"cost_low": None},
                    "standard_retail": {"cost_low": 0.0, "cost_high": 0.0},
                    "preferred_mail": {"cost_low": None},
                    "standard_mail": {"cost_low": None},
                },
            }
        ]
    )
    warnings = prose_channel_overclaim_warnings(
        "Cost is $0 across all CMS pharmacy channels.",
        coverage,
    )
    assert warnings
