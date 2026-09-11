from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from medicare_navigator.config import settings

_lock = threading.Lock()


def _feedback_path() -> Path:
    path = settings.data_dir / "feedback.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_feedback(
    *,
    message: str,
    state: str | None = None,
    zip_code: str | None = None,
) -> dict[str, Any]:
    entry = {
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "state": state.upper() if state else None,
        "zip": zip_code,
    }
    with _lock:
        with _feedback_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _as_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def read_feedback(
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Feedback entries, newest first. `since`/`until` filter by submission
    time (inclusive/exclusive respectively); `limit` caps the result count."""
    path = _feedback_path()
    if not path.is_file():
        return []

    entries: list[dict[str, Any]] = []
    with _lock:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))

    resolved_since = _as_naive_utc(since) if since is not None else None
    resolved_until = _as_naive_utc(until) if until is not None else None
    if resolved_since is not None or resolved_until is not None:
        filtered = []
        for entry in entries:
            submitted_at = _as_naive_utc(datetime.fromisoformat(entry["submitted_at"]))
            if resolved_since is not None and submitted_at < resolved_since:
                continue
            if resolved_until is not None and submitted_at >= resolved_until:
                continue
            filtered.append(entry)
        entries = filtered

    entries.sort(key=lambda e: e["submitted_at"], reverse=True)
    if limit is not None:
        entries = entries[:limit]
    return entries
