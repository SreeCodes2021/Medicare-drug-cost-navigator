"""Unit tests for the in-memory usage-analytics accumulator."""

import pytest

from medicare_navigator.analytics.collector import UsageCollector


def test_record_request_buckets_prompt_length_and_ok_error():
    c = UsageCollector()
    c.record_request(prompt_len=10, ok=True, latency_ms=5.0)
    c.record_request(prompt_len=100, ok=True, latency_ms=15.0)
    c.record_request(prompt_len=500, ok=False, latency_ms=25.0)

    acc = c.drain()
    assert len(acc.hourly) == 1
    counters = next(iter(acc.hourly.values()))
    assert counters.requests_total == 3
    assert counters.requests_ok == 2
    assert counters.requests_error == 1
    assert counters.prompt_len_short == 1
    assert counters.prompt_len_medium == 1
    assert counters.prompt_len_long == 1
    assert counters.latency_ms_sum == 45.0


def test_record_request_buckets_by_region():
    c = UsageCollector()
    c.record_request(prompt_len=10, ok=True, latency_ms=1.0, region="FL")
    c.record_request(prompt_len=10, ok=True, latency_ms=1.0, region="FL")
    c.record_request(prompt_len=10, ok=True, latency_ms=1.0, region="CA")
    c.record_request(prompt_len=10, ok=True, latency_ms=1.0)  # no region -> unknown

    acc = c.drain()
    by_region = {key[1]: counters.requests_total for key, counters in acc.hourly.items()}
    assert by_region == {"FL": 2, "CA": 1, "unknown": 1}


def test_record_request_buckets_by_mode_and_model():
    c = UsageCollector()
    c.record_request(prompt_len=10, ok=True, latency_ms=1.0, mode="chat", model="claude-sonnet-5")
    c.record_request(prompt_len=10, ok=True, latency_ms=1.0, mode="chat", model="claude-sonnet-5")
    c.record_request(prompt_len=10, ok=True, latency_ms=1.0, mode="guided_single", model="gpt-5.6-luna")
    c.record_request(prompt_len=10, ok=True, latency_ms=1.0)  # no mode/model -> unknown

    acc = c.drain()
    by_mode_model = {(key[2], key[3]): counters.requests_total for key, counters in acc.hourly.items()}
    assert by_mode_model == {
        ("chat", "claude-sonnet-5"): 2,
        ("guided_single", "gpt-5.6-luna"): 1,
        ("unknown", "unknown"): 1,
    }


def test_record_request_buckets_status_breakdown():
    c = UsageCollector()
    c.record_request(prompt_len=10, ok=True, latency_ms=1.0, status="ok")
    c.record_request(prompt_len=10, ok=True, latency_ms=1.0, status="needs_clarification")
    c.record_request(prompt_len=10, ok=True, latency_ms=1.0, status="not_found")
    c.record_request(prompt_len=10, ok=True, latency_ms=1.0, status="limit_reached")
    c.record_request(prompt_len=10, ok=False, latency_ms=1.0)

    acc = c.drain()
    counters = next(iter(acc.hourly.values()))
    assert counters.requests_total == 5
    assert counters.requests_ok == 4
    assert counters.requests_error == 1
    assert counters.requests_clarification == 1
    assert counters.requests_not_found == 1
    assert counters.requests_limit_reached == 1


def test_record_request_sums_tokens_and_cost():
    c = UsageCollector()
    c.record_request(prompt_len=10, ok=True, latency_ms=1.0, tokens_in=100, tokens_out=50, cost_usd=0.01)
    c.record_request(prompt_len=10, ok=True, latency_ms=1.0, tokens_in=200, tokens_out=75, cost_usd=0.02)

    acc = c.drain()
    counters = next(iter(acc.hourly.values()))
    assert counters.tokens_in_sum == 300
    assert counters.tokens_out_sum == 125
    assert counters.cost_usd_sum == pytest.approx(0.03)


def test_record_new_session_increments_sessions_new():
    c = UsageCollector()
    c.record_new_session()
    c.record_new_session()

    acc = c.drain()
    counters = next(iter(acc.hourly.values()))
    assert counters.sessions_new == 2


def test_drain_resets_accumulator_atomically():
    c = UsageCollector()
    c.record_request(prompt_len=10, ok=True, latency_ms=1.0)

    first = c.drain()
    assert first.hourly

    second = c.drain()
    assert second.hourly == {}
    assert second.query_log_rows == []


def test_record_query_log_queues_rows_without_writing():
    c = UsageCollector()
    c.record_query_log("q1", "sess-1", ["estimate_drug_cost"], {"estimate_drug_cost": "ok"}, 12.5)

    acc = c.drain()
    assert len(acc.query_log_rows) == 1
    row = acc.query_log_rows[0]
    assert row.query_id == "q1"
    assert row.session_id == "sess-1"
    assert row.tools_invoked == ["estimate_drug_cost"]
    assert row.latency_ms == 12.5
