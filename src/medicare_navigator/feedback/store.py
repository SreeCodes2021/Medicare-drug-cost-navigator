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
