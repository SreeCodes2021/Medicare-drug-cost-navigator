from __future__ import annotations

import asyncio
import json
import logging

from medicare_navigator.analytics.collector import _Accumulator, collector
from medicare_navigator.storage.connection import DuckDBConnection

log = logging.getLogger("uvicorn.error")


def _write_rows(acc: _Accumulator) -> None:
    db = DuckDBConnection()
    conn = db.connect()
    try:
        for (hour_bucket, region, mode, model), counters in acc.hourly.items():
            conn.execute(
                """
                INSERT INTO usage_hourly (
                    hour_bucket, region, mode, model, sessions_new, requests_total,
                    requests_ok, requests_error, requests_clarification, requests_not_found,
                    requests_limit_reached, prompt_len_short, prompt_len_medium, prompt_len_long,
                    prompt_len_sum, latency_ms_sum, tokens_in_sum, tokens_out_sum,
                    requests_with_tokens, cost_usd_sum
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (hour_bucket, region, mode, model) DO UPDATE SET
                    sessions_new = usage_hourly.sessions_new + excluded.sessions_new,
                    requests_total = usage_hourly.requests_total + excluded.requests_total,
                    requests_ok = usage_hourly.requests_ok + excluded.requests_ok,
                    requests_error = usage_hourly.requests_error + excluded.requests_error,
                    requests_clarification = usage_hourly.requests_clarification + excluded.requests_clarification,
                    requests_not_found = usage_hourly.requests_not_found + excluded.requests_not_found,
                    requests_limit_reached = usage_hourly.requests_limit_reached + excluded.requests_limit_reached,
                    prompt_len_short = usage_hourly.prompt_len_short + excluded.prompt_len_short,
                    prompt_len_medium = usage_hourly.prompt_len_medium + excluded.prompt_len_medium,
                    prompt_len_long = usage_hourly.prompt_len_long + excluded.prompt_len_long,
                    prompt_len_sum = usage_hourly.prompt_len_sum + excluded.prompt_len_sum,
                    latency_ms_sum = usage_hourly.latency_ms_sum + excluded.latency_ms_sum,
                    tokens_in_sum = usage_hourly.tokens_in_sum + excluded.tokens_in_sum,
                    tokens_out_sum = usage_hourly.tokens_out_sum + excluded.tokens_out_sum,
                    requests_with_tokens = usage_hourly.requests_with_tokens + excluded.requests_with_tokens,
                    cost_usd_sum = usage_hourly.cost_usd_sum + excluded.cost_usd_sum
                """,
                [
                    hour_bucket,
                    region,
                    mode,
                    model,
                    counters.sessions_new,
                    counters.requests_total,
                    counters.requests_ok,
                    counters.requests_error,
                    counters.requests_clarification,
                    counters.requests_not_found,
                    counters.requests_limit_reached,
                    counters.prompt_len_short,
                    counters.prompt_len_medium,
                    counters.prompt_len_long,
                    counters.prompt_len_sum,
                    counters.latency_ms_sum,
                    counters.tokens_in_sum,
                    counters.tokens_out_sum,
                    counters.requests_with_tokens,
                    counters.cost_usd_sum,
                ],
            )
        for row in acc.query_log_rows:
            conn.execute(
                "INSERT INTO query_log VALUES (?, ?, ?, ?, ?, current_timestamp)",
                [
                    row.query_id,
                    row.session_id,
                    json.dumps(row.tools_invoked),
                    json.dumps(row.statuses),
                    row.latency_ms,
                ],
            )
    finally:
        conn.close()


def flush_now() -> None:
    """Synchronous drain + write, for tests and manual invocation."""
    acc = collector.drain()
    if acc.hourly or acc.query_log_rows:
        _write_rows(acc)


async def flush_loop(interval_seconds: float) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        acc = collector.drain()
        if not acc.hourly and not acc.query_log_rows:
            continue
        try:
            await asyncio.to_thread(_write_rows, acc)
        except Exception:
            log.warning("analytics flush failed; dropping this batch", exc_info=True)
