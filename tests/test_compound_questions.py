"""Tests for compound-message deferral helpers."""

from medicare_navigator.agent.compound_questions import (
    is_compound_message,
    message_asks_distinct_subquestions,
    should_defer_deterministic_insulin,
    should_defer_resolver,
)
from medicare_navigator.agent.insulin_requests import resolve_insulin_request


def test_oop_plus_insulin_cost_is_compound_and_defers_oop():
    message = (
        "What's the CMS Part D annual out-of-pocket max for 2026, "
        "and how much will lantus cost me on plan S9999-001?"
    )
    assert is_compound_message(message)
    assert should_defer_resolver(message, "oop")
    assert not should_defer_resolver(message, "tier")


def test_pharmacy_plus_insulin_policy_defers_pharmacy():
    message = (
        "Is insulin always capped at $35, and what pharmacies near zip 32801 "
        "carry lantus on plan S9999-001?"
    )
    assert should_defer_resolver(message, "pharmacy")
    assert should_defer_resolver(message, "insulin_policy")


def test_integrated_pharmacy_cost_does_not_defer_insulin():
    message = (
        "What's the cost of lantus at my nearest preferred pharmacy in zip 32801 "
        "on plan S9999-001, for the rest of the year?"
    )
    insulin = resolve_insulin_request(message)
    assert insulin is not None
    assert not should_defer_deterministic_insulin(
        message,
        insulin,
        has_unhandled_date_window=True,
    )


def test_tier_plus_remaining_year_defers_insulin():
    message = (
        "What tier is lantus on plan S9999-001, "
        "and how much will it cost me for the rest of the year?"
    )
    insulin = resolve_insulin_request(message)
    assert insulin is not None
    assert should_defer_deterministic_insulin(
        message,
        insulin,
        has_unhandled_date_window=True,
    )


def test_numbered_triple_counts_as_distinct_subquestions():
    message = (
        "I have three quick questions: (1) What's the CMS Part D out-of-pocket max "
        "for 2026? (2) What pharmacies are near zip 32801? "
        "(3) How much is lantus on plan S9999-001?"
    )
    assert message_asks_distinct_subquestions(message)
    assert should_defer_resolver(message, "oop")
