from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from medicare_navigator.config import settings

DEFAULT_BASE_URL = "http://localhost:8000"

# Element IDs required in index.html.
REQUIRED_ELEMENT_IDS = [
    "disclaimer-banner",
    "disclaimer-text",
    "filters-panel",
    "filter-badge",
    "toggle-filters",
    "filters-body",
    "filter-drug",
    "filter-dosage",
    "filter-plan",
    "filter-year",
    "filter-ytd",
    "filter-alternatives",
    "filter-trend",
    "turn-counter",
    "chat-messages",
    "empty-state",
    "loading",
    "loading-text",
    "chat-form",
    "chat-input",
    "send-btn",
    "results-panel",
    "data-as-of",
    "results-content",
]

# Subset app.js must reference via getElementById / el("…").
JS_REFERENCED_ELEMENT_IDS = [
    eid
    for eid in REQUIRED_ELEMENT_IDS
    if eid not in {"disclaimer-banner", "filters-panel", "results-panel"}
]

REQUIRED_STATIC_PATHS = ["/", "/app.js", "/styles.css"]

REQUIRED_API_PATHS = [
    "/api/health",
    "/api/disclaimer",
    "/api/plans",
    "/api/meta/as-of",
]

# Fields app.js reads from /api/chat responses.
CHAT_RESPONSE_UI_FIELDS = [
    "status",
    "explanation",
    "clarification_message",
    "formulary",
    "cost_trend",
    "alternatives",
    "citations",
    "data_as_of",
    "tool_statuses",
    "response_source",
    "drug_name",
    "rxcui",
]

SMOKE_MESSAGES = [
    {
        "name": "tier_lookup",
        "message": "What's the tier and copay for metformin 500mg on plan H1234-045?",
        "expect_statuses": {"ok", "needs_clarification"},
    },
    {
        "name": "chip_prompt_alternatives",
        "message": "Show alternatives to lipitor",
        "expect_statuses": {"ok", "needs_clarification", "not_found"},
    },
]


class HttpGetter(Protocol):
    def get(self, path: str) -> tuple[int, str]: ...

    def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    group: str = "general"


@dataclass
class CheckReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]

    def add(self, name: str, passed: bool, detail: str = "", group: str = "general") -> None:
        self.results.append(CheckResult(name=name, passed=passed, detail=detail, group=group))

    def merge(self, other: CheckReport) -> None:
        self.results.extend(other.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.passed,
            "total": len(self.results),
            "passed": sum(1 for r in self.results if r.passed),
            "failed": [r.__dict__ for r in self.failed],
            "results": [r.__dict__ for r in self.results],
        }


class HttpxGetter:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def get(self, path: str) -> tuple[int, str]:
        response = self._client.get(path)
        return response.status_code, response.text

    def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        response = self._client.post(path, json=payload)
        return response.status_code, response.json()

    def close(self) -> None:
        self._client.close()


class InProcessGetter:
    """In-process FastAPI client (offline pytest / medicare-ui-test --offline)."""

    def __init__(self):
        from fastapi.testclient import TestClient

        from medicare_navigator.api.app import app

        self._client = TestClient(app)

    def get(self, path: str) -> tuple[int, str]:
        response = self._client.get(path)
        return response.status_code, response.text

    def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        response = self._client.post(path, json=payload)
        return response.status_code, response.json()

    def close(self) -> None:
        pass


def frontend_dist_dir() -> Path:
    return settings.project_root / "frontend" / "dist"


def check_static_files_on_disk() -> CheckReport:
    report = CheckReport()
    dist = frontend_dist_dir()
    for name in ("index.html", "app.js", "styles.css"):
        path = dist / name
        report.add(
            f"disk:{name}",
            path.is_file() and path.stat().st_size > 0,
            detail=str(path),
            group="static",
        )
    return report


def check_html_element_contract(html: str) -> CheckReport:
    report = CheckReport()
    for element_id in REQUIRED_ELEMENT_IDS:
        found = f'id="{element_id}"' in html or f"id='{element_id}'" in html
        report.add(
            f"html:id:{element_id}",
            found,
            detail="missing from index.html" if not found else "",
            group="static",
        )

    chip_count = len(re.findall(r'class="chip"', html))
    report.add(
        "html:prompt_chips",
        chip_count >= 3,
        detail=f"found {chip_count} chip buttons, expected >= 3",
        group="static",
    )
    return report


def check_app_js_contract(js: str) -> CheckReport:
    report = CheckReport()
    for element_id in JS_REFERENCED_ELEMENT_IDS:
        report.add(
            f"js:refs:{element_id}",
            element_id in js,
            detail="not referenced in app.js" if element_id not in js else "",
            group="static",
        )

    for api_path in ("/api/disclaimer", "/api/plans", "/api/chat"):
        report.add(
            f"js:fetch:{api_path}",
            api_path in js,
            detail="fetch path missing" if api_path not in js else "",
            group="static",
        )

    for fn in ("loadDisclaimer", "loadPlans", "sendMessage", "renderResults", "getFilters"):
        report.add(
            f"js:function:{fn}",
            f"function {fn}" in js or f"async function {fn}" in js,
            detail="function missing" if fn not in js else "",
            group="static",
        )
    return report


def check_static_served(getter: HttpGetter) -> CheckReport:
    report = CheckReport()
    for path in REQUIRED_STATIC_PATHS:
        status, body = getter.get(path)
        report.add(
            f"served:{path}",
            status == 200 and len(body) > 0,
            detail=f"status={status}, len={len(body)}",
            group="static",
        )

    status, html = getter.get("/")
    if status == 200:
        report.merge(check_html_element_contract(html))

    status, js = getter.get("/app.js")
    if status == 200:
        report.merge(check_app_js_contract(js))
    return report


def check_api_contract(getter: HttpGetter) -> CheckReport:
    report = CheckReport()

    for path in REQUIRED_API_PATHS:
        status, body = getter.get(path)
        report.add(
            f"api:get:{path}",
            status == 200,
            detail=f"status={status}",
            group="api",
        )

    status, disclaimer_body = getter.get("/api/disclaimer")
    if status == 200:
        import json

        data = json.loads(disclaimer_body)
        text = data.get("text", "")
        report.add(
            "api:disclaimer:text",
            bool(text.strip()),
            detail="empty disclaimer text",
            group="api",
        )

    status, plans_body = getter.get("/api/plans")
    if status == 200:
        import json

        plans = json.loads(plans_body)
        report.add(
            "api:plans:nonempty",
            isinstance(plans, list) and len(plans) > 0,
            detail=f"plan count={len(plans) if isinstance(plans, list) else 'n/a'}",
            group="api",
        )
        if isinstance(plans, list) and plans:
            sample = plans[0]
            for key in ("plan_key", "plan_name"):
                report.add(
                    f"api:plans:field:{key}",
                    key in sample,
                    detail=f"missing on first plan: {sample}",
                    group="api",
                )
    return report


def check_chat_smoke(getter: HttpGetter, *, timeout_note: str = "") -> CheckReport:
    report = CheckReport()

    for case in SMOKE_MESSAGES:
        status, data = getter.post_json("/api/chat", {"message": case["message"]})
        ok_status = status == 200
        report.add(
            f"chat:{case['name']}:http",
            ok_status,
            detail=f"status={status}{timeout_note}",
            group="chat",
        )
        if not ok_status:
            continue

        for key in ("session_id", "turn_count", "response"):
            report.add(
                f"chat:{case['name']}:envelope:{key}",
                key in data,
                detail=f"missing {key}",
                group="chat",
            )

        inner = data.get("response") or {}
        resp_status = inner.get("status")
        report.add(
            f"chat:{case['name']}:status",
            resp_status in case["expect_statuses"],
            detail=f"got {resp_status!r}",
            group="chat",
        )

        shown = inner.get("explanation") or inner.get("clarification_message") or ""
        report.add(
            f"chat:{case['name']}:visible_text",
            bool(str(shown).strip()),
            detail="no explanation or clarification_message for UI",
            group="chat",
        )

        for field_name in CHAT_RESPONSE_UI_FIELDS:
            report.add(
                f"chat:{case['name']}:field:{field_name}",
                field_name in inner,
                detail="missing key (UI may break)",
                group="chat",
            )

        if data.get("session_id") and resp_status == "ok":
            status2, data2 = getter.post_json(
                "/api/chat",
                {
                    "message": "what if I've spent $400 YTD?",
                    "session_id": data["session_id"],
                    "filters": {"plan_id": "H1234-045", "drug": "metformin", "dosage": "500mg"},
                },
            )
            report.add(
                f"chat:{case['name']}:follow_up:http",
                status2 == 200,
                detail=f"status={status2}",
                group="chat",
            )
            if status2 == 200:
                report.add(
                    f"chat:{case['name']}:follow_up:turn_increment",
                    (data2.get("turn_count") or 0) > (data.get("turn_count") or 0),
                    detail=f"turn {data.get('turn_count')} -> {data2.get('turn_count')}",
                    group="chat",
                )
            break

    return report


def _ensure_deterministic_llm() -> None:
    """Match tests/conftest.py so offline UI checks do not call external LLMs."""
    settings.anthropic_api_key = ""
    settings.openai_api_key = ""


def run_checks(
    *,
    groups: set[str] | None = None,
    offline: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 120.0,
) -> CheckReport:
    """Run selected UI check groups: static, api, chat."""
    if offline:
        _ensure_deterministic_llm()
    selected = groups or {"static", "api", "chat"}
    report = CheckReport()

    if "static" in selected:
        report.merge(check_static_files_on_disk())
        getter: HttpGetter
        if offline:
            getter = InProcessGetter()
        else:
            getter = HttpxGetter(base_url, timeout=timeout)
        try:
            report.merge(check_static_served(getter))
        finally:
            getter.close()

    if "api" in selected or "chat" in selected:
        getter = InProcessGetter() if offline else HttpxGetter(base_url, timeout=timeout)
        try:
            if "api" in selected:
                report.merge(check_api_contract(getter))
            if "chat" in selected:
                report.merge(check_chat_smoke(getter))
        finally:
            getter.close()

    return report
