from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

PROMPT_LEN_SHORT_MAX = 50
PROMPT_LEN_MEDIUM_MAX = 200


@dataclass
class HourlyCounters:
    sessions_new: int = 0
    requests_total: int = 0
    requests_ok: int = 0
    requests_error: int = 0
    requests_clarification: int = 0
    requests_not_found: int = 0
    requests_limit_reached: int = 0
    prompt_len_short: int = 0
    prompt_len_medium: int = 0
    prompt_len_long: int = 0
    latency_ms_sum: float = 0.0
    tokens_in_sum: int = 0
    tokens_out_sum: int = 0
    cost_usd_sum: float = 0.0


@dataclass
class QueryLogRow:
    query_id: str
    session_id: str
    tools_invoked: list[str]
    statuses: dict[str, str]
    latency_ms: float


@dataclass
class _Accumulator:
    hourly: dict[tuple[datetime, str, str, str], HourlyCounters] = field(default_factory=dict)
    query_log_rows: list[QueryLogRow] = field(default_factory=list)


class UsageCollector:
    """In-memory, aggregate-only usage accumulator.

    No message text, drug names, or identifying data is ever recorded here —
    only counts and coarse buckets, consistent with the app's privacy policy.
    Reads/writes are drained periodically by analytics.flush, never written to
    disk inline on the request path.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._acc = _Accumulator()

    def _bucket(self, region: str, mode: str = "unknown", model: str = "unknown") -> tuple[datetime, str, str, str]:
        hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        return hour.replace(tzinfo=None), region, mode or "unknown", model or "unknown"

    def record_new_session(self) -> None:
        with self._lock:
            counters = self._acc.hourly.setdefault(self._bucket("unknown"), HourlyCounters())
            counters.sessions_new += 1

    def record_request(
        self,
        *,
        prompt_len: int,
        ok: bool,
        latency_ms: float,
        region: str = "unknown",
        mode: str = "unknown",
        model: str = "unknown",
        status: str = "ok",
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        region = region or "unknown"
        with self._lock:
            counters = self._acc.hourly.setdefault(self._bucket(region, mode, model), HourlyCounters())
            counters.requests_total += 1
            if ok:
                counters.requests_ok += 1
                if status == "needs_clarification":
                    counters.requests_clarification += 1
                elif status == "not_found":
                    counters.requests_not_found += 1
                elif status == "limit_reached":
                    counters.requests_limit_reached += 1
            else:
                counters.requests_error += 1
            if prompt_len < PROMPT_LEN_SHORT_MAX:
                counters.prompt_len_short += 1
            elif prompt_len < PROMPT_LEN_MEDIUM_MAX:
                counters.prompt_len_medium += 1
            else:
                counters.prompt_len_long += 1
            counters.latency_ms_sum += latency_ms
            counters.tokens_in_sum += tokens_in
            counters.tokens_out_sum += tokens_out
            counters.cost_usd_sum += cost_usd

    def record_query_log(
        self,
        query_id: str,
        session_id: str,
        tools_invoked: list[str],
        statuses: dict[str, str],
        latency_ms: float,
    ) -> None:
        with self._lock:
            self._acc.query_log_rows.append(
                QueryLogRow(query_id, session_id, tools_invoked, statuses, latency_ms)
            )

    def drain(self) -> _Accumulator:
        with self._lock:
            drained = self._acc
            self._acc = _Accumulator()
            return drained


collector = UsageCollector()
