from __future__ import annotations

import json
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"


def check_health(base_url: str = DEFAULT_BASE_URL, timeout: float = 5.0) -> dict[str, Any]:
    """Return /api/health payload or raise on connection failure."""
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        response = client.get("/api/health")
        response.raise_for_status()
        return response.json()


def invoke_chat(
    message: str,
    *,
    session_id: str | None = None,
    filters: dict[str, Any] | None = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """POST /api/chat and return a grading-oriented bundle."""
    payload: dict[str, Any] = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    if filters:
        payload["filters"] = filters

    with httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        response = client.post("/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()

    return build_grading_bundle(message, data)


def build_grading_bundle(user_message: str, chat_response: dict[str, Any]) -> dict[str, Any]:
    """Shape /api/chat JSON into fields the chat-QA rubric expects."""
    inner = chat_response.get("response") or {}
    shown_text = inner.get("explanation") or inner.get("clarification_message") or ""

    return {
        "user_message": user_message,
        "session_id": chat_response.get("session_id"),
        "turn_count": chat_response.get("turn_count"),
        "grading": {
            "explanation": shown_text,
            "status": inner.get("status"),
            "clarification_message": inner.get("clarification_message"),
            "citations": inner.get("citations") or [],
            "estimate": inner.get("estimate"),
            "data_as_of": inner.get("data_as_of") or {},
            "tool_statuses": inner.get("tool_statuses") or {},
            "tools_invoked": inner.get("tools_invoked") or [],
            "response_source": inner.get("response_source"),
            "disclaimer": inner.get("disclaimer"),
            "drug_name": inner.get("drug_name"),
            "rxcui": inner.get("rxcui"),
        },
        "raw": chat_response,
    }


def bundle_to_json(bundle: dict[str, Any], *, indent: int = 2) -> str:
    return json.dumps(bundle, indent=indent, default=str)
