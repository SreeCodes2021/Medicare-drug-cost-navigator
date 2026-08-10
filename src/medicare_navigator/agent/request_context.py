from __future__ import annotations

from contextvars import ContextVar

_request_timezone: ContextVar[str | None] = ContextVar("request_timezone", default=None)


def set_request_timezone(timezone: str | None) -> None:
    _request_timezone.set(timezone)


def get_request_timezone() -> str | None:
    return _request_timezone.get()
