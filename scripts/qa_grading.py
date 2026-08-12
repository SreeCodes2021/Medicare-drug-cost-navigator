"""Shared helpers for live-LLM quality-test runners (T3 batch grading).

Used by `scripts/run_quality_test_llm.py` and `scripts/run_llm_scenarios.py`.
Do not create ad-hoc `tmp_t3_*.py` scripts — extend this module or the suite
JSON under `scripts/llm_scenario_suites/` instead.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnResult:
    message: str
    ok: bool
    session_id: str | None = None
    status: str | None = None
    response_source: str | None = None
    explanation: str = ""
    tools_invoked: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class Finding:
    section: str
    verdict: str  # BLOCK | REVISE
    issue: str
    msg: str | None = None
    turn: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "section": self.section,
            "verdict": self.verdict,
            "issue": self.issue,
        }
        if self.msg is not None:
            out["msg"] = self.msg
        if self.turn is not None:
            out["turn"] = self.turn
        if self.error is not None:
            out["error"] = self.error
        return out


def grading(bundle: dict[str, Any]) -> dict[str, Any]:
    return bundle.get("grading") or {}


def explanation(bundle: dict[str, Any]) -> str:
    return grading(bundle).get("explanation") or ""


def response_source(bundle: dict[str, Any]) -> str:
    return grading(bundle).get("response_source") or ""


def tools_invoked(bundle: dict[str, Any]) -> list[str]:
    return grading(bundle).get("tools_invoked") or []


def dollars(text: str) -> list[float]:
    return [
        float(x.replace("$", "").replace(",", ""))
        for x in re.findall(r"\$[\d,]+(?:\.\d{2})?", text)
    ]


def has_disclaimer(text: str) -> bool:
    lower = text.lower()
    return "not medical advice" in lower or "not a substitute" in lower


def prose_echoes_injected_dollar(
    text: str,
    amount: float,
    *,
    prefix_no_not: int = 50,
) -> bool:
    """Detect when prose states an injected price as fact (not $15 falsely matching $1)."""
    if amount == 0:
        pattern = re.compile(r"\$0(?!\.\d)")
    elif amount == int(amount):
        pattern = re.compile(rf"\${int(amount)}(?:\.\d{{2}})?(?!\d)")
    else:
        pattern = re.compile(rf"\${amount:.2f}(?!\d)")
    if not pattern.search(text):
        return False
    return "not" not in text.lower()[:prefix_no_not]


def tier_mentioned(text: str, tier: int | float) -> bool:
    return bool(re.search(rf"\btier\s*{int(tier)}\b", text, re.I))


def http_post_json(base_url: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def oracle_estimate(
    base_url: str,
    plan_id: str,
    drug: str,
    *,
    dosage: str = "",
    ytd: float = 0,
    days_supply: int = 30,
) -> dict[str, Any] | None:
    body: dict[str, Any] = {
        "plan_id": plan_id,
        "drug": drug,
        "days_supply": days_supply,
        "ytd_oop_spend": ytd,
    }
    if dosage:
        body["dosage"] = dosage
    result = http_post_json(base_url, "/api/estimate", body)
    return result.get("data")


def invoke_chat(
    message: str,
    *,
    model: str,
    session_id: str | None = None,
    filters_json: dict[str, Any] | None = None,
) -> TurnResult:
    cmd = [
        "medicare-chat-invoke",
        "send",
        "--message",
        message,
        "--model",
        model,
    ]
    if session_id:
        cmd.extend(["--session-id", session_id])
    if filters_json:
        cmd.extend(["--filters-json", json.dumps(filters_json)])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
    except FileNotFoundError:
        return TurnResult(
            message=message,
            ok=False,
            error="medicare-chat-invoke not found — pip install -e '.[dev]'",
        )
    except subprocess.TimeoutExpired:
        return TurnResult(message=message, ok=False, error="invoke timed out")

    if proc.returncode != 0:
        return TurnResult(
            message=message,
            ok=False,
            error=(proc.stderr or proc.stdout or "invoke failed").strip(),
        )

    try:
        bundle = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return TurnResult(message=message, ok=False, error=f"invalid JSON from invoke: {exc}")

    if not bundle.get("ok"):
        return TurnResult(
            message=message,
            ok=False,
            error=str(bundle.get("error") or "invoke returned ok=false"),
            raw=bundle,
        )

    g = grading(bundle)
    return TurnResult(
        message=message,
        ok=True,
        session_id=bundle.get("session_id"),
        status=g.get("status"),
        response_source=g.get("response_source"),
        explanation=g.get("explanation") or "",
        tools_invoked=g.get("tools_invoked") or [],
        raw=bundle,
    )


def check_health() -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["medicare-chat-invoke", "health"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return False, proc.stderr or proc.stdout or "health check failed"
        data = json.loads(proc.stdout)
        if not data.get("ok"):
            return False, "medicare-chat-invoke health returned ok=false"
        health = data.get("health") or {}
        if not health.get("llm_configured"):
            return False, "llm_configured is false — enable real LLM before grading"
        return True, "ok"
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return False, str(exc)
