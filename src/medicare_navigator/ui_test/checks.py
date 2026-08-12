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
    "main-panel",
    "mode-tab-chat",
    "mode-tab-guided",
    "mode-chat",
    "mode-guided",
    "guided-body",
    "guided-error",
    "guided-submit",
    "filter-drug",
    "filter-drug-input",
    "filter-drug-panel",
    "filter-drug-filter",
    "filter-dosage",
    "filter-dosage-input",
    "filter-drug-listbox",
    "filter-dosage-listbox",
    "filter-plan",
    "filter-plan-input",
    "filter-plan-listbox",
    "refresh-plans",
    "plan-load-hint",
    "data-release-label",
    "filter-ytd",
    "filter-days-supply",
    "turn-counter",
    "model-select",
    "session-usage",
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
    if eid not in {"disclaimer-banner", "main-panel", "results-panel", "mode-chat", "mode-guided", "guided-body"}
]

REQUIRED_STATIC_PATHS = ["/", "/app.js", "/styles.css"]
GUIDED_ELEMENT_IDS = [
    "guided-mode-single",
    "guided-mode-multidrug",
    "guided-mode-compareplans",
    "guided-single",
    "guided-multidrug",
    "guided-compareplans",
    "md-plan-input",
    "md-plan",
    "md-plan-listbox",
    "multidrug-rows",
    "multidrug-add-row",
    "multidrug-submit",
    "md-days-supply",
    "md-ytd",
    "compareplans-rows",
    "compareplans-add-row",
    "compareplans-submit",
    "cp-drug",
    "cp-drug-input",
    "cp-drug-panel",
    "cp-drug-filter",
    "cp-drug-listbox",
    "cp-dosage",
    "cp-dosage-input",
    "cp-dosage-listbox",
    "cp-days-supply",
    "cp-ytd",
    "guided-conversation",
    "guided-chat-messages",
    "guided-chat-form",
    "guided-chat-input",
    "guided-send-btn",
    "guided-turn-counter",
    "guided-results-panel",
    "guided-results-content",
    "guided-model-select",
]

# State (required) + zip (optional prefill) plan-discovery widgets — Guided form and Chat.
# Never sent to /api/estimate*, /api/estimate-batch, or /api/compare-plans.
LOCATION_PICKER_ELEMENT_IDS = [
    "guided-location-picker",
    "guided-state-input",
    "guided-state",
    "guided-state-listbox",
    "guided-zip-input",
    "guided-zip-caution",
    "chat-location-picker",
    "chat-state-input",
    "chat-state",
    "chat-state-listbox",
    "chat-zip-input",
    "chat-zip-caution",
    "chat-plan-input",
    "chat-plan",
    "chat-plan-listbox",
]

REQUIRED_API_PATHS = [
    "/api/health",
    "/api/disclaimer",
    "/api/privacy",
    "/api/plans",
    "/api/models",
    "/api/meta/as-of",
    "/api/states",
    "/api/drugs",
]

# Fields app.js reads from /api/chat responses.
CHAT_RESPONSE_UI_FIELDS = [
    "status",
    "explanation",
    "clarification_message",
    "estimate",
    "citations",
    "data_as_of",
    "tool_statuses",
    "response_source",
    "llm_usage",
    "mediator_llm_usage",
    "total_llm_usage",
    "drug_name",
    "rxcui",
    "channel_estimate",
    "channel_estimates",
]

SMOKE_MESSAGES = [
    {
        "name": "tier_lookup",
        "message": "What's the cost for metformin 500mg on plan H8888-001?",
        "expect_statuses": {"ok", "needs_clarification", "not_found"},
    },
    {
        "name": "quantity_limit_prompt",
        "message": "What's the cost for januvia 100mg on plan S9999-001 for a 90 day supply?",
        "expect_statuses": {"ok", "needs_clarification", "not_found"},
    },
]

# Payloads mirroring guided form submission in frontend/src/app.js.
GUIDED_SMOKE_FLOWS = [
    {
        "name": "guided_single",
        "message": "What's the cost for metformin 500mg on plan S9999-001?",
        "filters": {
            "drug": "metformin",
            "dosage": "500mg",
            "plan_id": "S9999-001",
            "days_supply": 30,
        },
        "expect_statuses": {"ok", "needs_clarification", "not_found"},
    },
    {
        "name": "guided_multi",
        "message": (
            "Estimate costs for metformin 500mg, januvia 100mg on plan S9999-001. "
            "Use a 30-day supply and $0 year-to-date out-of-pocket spending. "
            "Summarize each drug and the combined cost."
        ),
        "filters": None,
        "expect_statuses": {"ok", "needs_clarification", "not_found"},
    },
    {
        "name": "guided_compare",
        "message": (
            "Compare the cost of metformin 500mg across these Medicare plans: "
            "S9999-001, H8888-001. Use a 30-day supply and $0 year-to-date "
            "out-of-pocket spending. Summarize the differences and identify "
            "the lowest estimated cost."
        ),
        "filters": {
            "drug": "metformin",
            "dosage": "500mg",
            "days_supply": 30,
            "ytd_oop_spend": 0,
        },
        "expect_statuses": {"ok", "needs_clarification", "not_found"},
    },
]

BROWSER_FLOW_NAMES = ("chat", "guided-single", "guided-multi", "guided-compare-plan")

# Every text/number input and select-like control a beneficiary can type into
# or open, across chat and all three guided submodes. Tier-1 smoke: exists,
# accepts blank/whitespace, and (for pickers) offers options once data loads.
SMOKE_TEXT_INPUT_IDS = [
    "chat-input",
    "filter-drug-input",
    "filter-drug-filter",
    "filter-dosage-input",
    "filter-plan-input",
    "filter-ytd",
    "guided-chat-input",
    "guided-state-input",
    "guided-zip-input",
    "chat-state-input",
    "chat-zip-input",
    "md-ytd",
    "cp-drug-input",
    "cp-drug-filter",
    "cp-dosage-input",
    "cp-ytd",
]

SMOKE_SELECT_IDS = [
    "model-select",
    "guided-model-select",
    "filter-days-supply",
    "md-days-supply",
    "cp-days-supply",
]

SMOKE_BLANK_SUBMIT_CASES = [
    # (name, method, path, payload_or_params, expected_status_codes)
    ("estimate_blank_drug", "post", "/api/estimate", {"plan_id": "S9999-001", "drug": ""}, {400, 422}),
    ("estimate_blank_plan", "post", "/api/estimate", {"plan_id": "", "drug": "metformin"}, {400, 422}),
    ("drug_dosages_blank_drug", "get", "/api/drug-dosages?drug=", None, {400}),
    ("drug_dosages_whitespace_drug", "get", "/api/drug-dosages?drug=%20%20", None, {400}),
    ("chat_blank_message", "post", "/api/chat", {"message": ""}, {200, 400, 422}),
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


def check_guided_ui_contract(html: str, js: str, css: str) -> CheckReport:
    """Check the guided estimate's controls and interaction guardrails."""
    report = CheckReport()
    for element_id in GUIDED_ELEMENT_IDS:
        found = f'id="{element_id}"' in html or f"id='{element_id}'" in html
        report.add(
            f"guided:html:id:{element_id}",
            found,
            detail="missing from guided estimate UI" if not found else "",
            group="guided",
        )

    for element_id in LOCATION_PICKER_ELEMENT_IDS:
        found = f'id="{element_id}"' in html or f"id='{element_id}'" in html
        report.add(
            f"location:html:id:{element_id}",
            found,
            detail="missing state/zip picker element" if not found else "",
            group="guided",
        )

    for fn in (
        "switchGuidedSubmode",
        "submitMultiDrugEstimate",
        "submitComparePlans",
        "createPlanCombobox",
        "createStateCombobox",
        "wireZipPicker",
        "loadStates",
        "lookupZipState",
        "initLocationPickers",
        "resetGuidedFields",
        "resetGuidedConversation",
        "sendGuidedInitial",
        "sendGuidedMessage",
        "isGuidedSingleValid",
        "isGuidedMultiDrugValid",
        "isGuidedComparePlansValid",
        "updateGuidedSubmitButtonState",
    ):
        found = f"function {fn}" in js or f"async function {fn}" in js
        report.add(
            f"guided:js:function:{fn}",
            found,
            detail="guided interaction function missing" if not found else "",
            group="guided",
        )

    report.add(
        "guided:css:top-level-tabs",
        ".primary-mode-tabs" in css and ".primary-mode-tab.active" in css,
        detail="top-level Chat and Guided form tab styles are required",
        group="guided",
    )
    report.add(
        "guided:css:no-overlay-sheet",
        ".guided-sheet-backdrop" not in css and ".guided-sheet {" not in css,
        detail="guided form must be a normal page, not an overlay sheet",
        group="guided",
    )
    report.add(
        "guided:js:fresh-session",
        "guidedSessionId = null" in js and "resetGuidedConversation();" in js,
        detail="each guided estimate must start a fresh session",
        group="guided",
    )
    report.add(
        "guided:js:five-turn-limit",
        "guidedTurnCount < 5" in js and "guidedTurnCount >= 5" in js,
        detail="guided conversation must stop after five total turns",
        group="guided",
    )
    for button_id in ("guided-submit", "multidrug-submit", "compareplans-submit"):
        report.add(
            f"guided:html:submit-disabled:{button_id}",
            f'id="{button_id}"' in html and " disabled" in html.split(f'id="{button_id}"', 1)[1].split(">", 1)[0],
            detail=f"{button_id} must start disabled until required fields are filled",
            group="guided",
        )
    report.add(
        "guided:js:submit-validation",
        "guidedEstimateInFlight" in js
        and "updateGuidedSubmitButtonState()" in js
        and "promptGuidedMandatoryFields" in js,
        detail="guided submit buttons must reflect form validity and in-flight state",
        group="guided",
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

    for fn in (
        "loadDisclaimer",
        "loadPlans",
        "sendMessage",
        "renderResults",
        "getFilters",
        "switchMode",
        "submitGuidedEstimate",
    ):
        report.add(
            f"js:function:{fn}",
            f"function {fn}" in js or f"async function {fn}" in js,
            detail="function missing" if fn not in js else "",
            group="static",
        )
    return report


def check_field_element_contract(html: str) -> CheckReport:
    """Every smoke-tracked input/select id actually exists in the shipped HTML."""
    report = CheckReport()
    for element_id in SMOKE_TEXT_INPUT_IDS + SMOKE_SELECT_IDS:
        found = f'id="{element_id}"' in html or f"id='{element_id}'" in html
        report.add(
            f"fields:html:id:{element_id}",
            found,
            detail="missing input/select id" if not found else "",
            group="fields",
        )
    return report


def check_field_blank_whitespace_handling(getter: HttpGetter) -> CheckReport:
    """Blank/whitespace field values must be rejected or handled gracefully —
    never a 500 or an unhandled exception. This is the "smoke" bar (doesn't
    crash), not the "functional" bar (does the right thing)."""
    report = CheckReport()
    for name, method, path, payload, expected_statuses in SMOKE_BLANK_SUBMIT_CASES:
        if method == "get":
            status, _ = getter.get(path)
        else:
            status, _ = getter.post_json(path, payload or {})
        report.add(
            f"fields:blank:{name}",
            status in expected_statuses and status != 500,
            detail=f"status={status}, expected one of {sorted(expected_statuses)}",
            group="fields",
        )
    return report


def check_model_selects_populated(getter: HttpGetter) -> CheckReport:
    """#model-select / #guided-model-select must have >=1 non-empty option,
    sourced from GET /api/models — otherwise the dropdown renders empty."""
    report = CheckReport()
    status, body = getter.get("/api/models")
    report.add("fields:models:http", status == 200, detail=f"status={status}", group="fields")
    if status == 200:
        import json

        data = json.loads(body)
        models = data.get("models") or []
        report.add(
            "fields:models:nonempty",
            isinstance(models, list) and len(models) > 0,
            detail=f"models={models}",
            group="fields",
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

    status, privacy_body = getter.get("/api/privacy")
    if status == 200:
        import json

        data = json.loads(privacy_body)
        text = data.get("text", "")
        report.add(
            "api:privacy:text",
            bool(text.strip()),
            detail="empty privacy policy text",
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
            for key in ("plan_key", "plan_name", "state"):
                report.add(
                    f"api:plans:field:{key}",
                    key in sample,
                    detail=f"missing on first plan: {sample}",
                    group="api",
                )

    status, states_body = getter.get("/api/states")
    if status == 200:
        import json

        data = json.loads(states_body)
        report.add(
            "api:states:shape",
            isinstance(data.get("states"), list),
            detail=f"body={data}",
            group="api",
        )

    status, zip_body = getter.get("/api/zip-lookup?zip=72201")
    report.add(
        "api:zip-lookup:known_zip",
        status == 200,
        detail=f"status={status}",
        group="api",
    )
    if status == 200:
        import json

        data = json.loads(zip_body)
        report.add(
            "api:zip-lookup:resolves_state",
            data.get("state") == "AR",
            detail=f"body={data}",
            group="api",
        )

    status, drugs_body = getter.get("/api/drugs")
    if status == 200:
        import json

        data = json.loads(drugs_body)
        report.add(
            "api:drugs:shape",
            isinstance(data.get("drugs"), list) and "metformin" in data.get("drugs", []),
            detail=f"body={data}",
            group="api",
        )

    status, dosages_body = getter.get("/api/drug-dosages?drug=metformin")
    report.add(
        "api:drug-dosages:known_drug",
        status == 200,
        detail=f"status={status}",
        group="api",
    )
    if status == 200:
        import json

        data = json.loads(dosages_body)
        report.add(
            "api:drug-dosages:shape",
            isinstance(data.get("dosages"), list),
            detail=f"body={data}",
            group="api",
        )

    status, estimate_body = getter.post_json(
        "/api/estimate",
        {"plan_id": "S9999-001", "drug": "metformin", "dosage": "500mg", "days_supply": 30, "ytd_oop_spend": 0},
    )
    report.add(
        "api:estimate:post",
        status == 200
        and estimate_body.get("status") in {"ok", "needs_clarification", "not_found"},
        detail=f"status={status}, body_status={estimate_body.get('status')}",
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
                    "filters": {"plan_id": "H8888-001", "drug": "metformin", "dosage": "500mg"},
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


def check_guided_smoke(getter: HttpGetter, *, timeout_note: str = "") -> CheckReport:
    """POST /api/chat with guided-form message templates (API parity with UI)."""
    report = CheckReport()

    for case in GUIDED_SMOKE_FLOWS:
        payload: dict[str, Any] = {"message": case["message"]}
        if case.get("filters"):
            payload["filters"] = case["filters"]

        status, data = getter.post_json("/api/chat", payload)
        ok_status = status == 200
        report.add(
            f"guided:{case['name']}:http",
            ok_status,
            detail=f"status={status}{timeout_note}",
            group="guided",
        )
        if not ok_status:
            continue

        for key in ("session_id", "turn_count", "response"):
            report.add(
                f"guided:{case['name']}:envelope:{key}",
                key in data,
                detail=f"missing {key}",
                group="guided",
            )

        inner = data.get("response") or {}
        resp_status = inner.get("status")
        report.add(
            f"guided:{case['name']}:status",
            resp_status in case["expect_statuses"],
            detail=f"got {resp_status!r}",
            group="guided",
        )

        shown = inner.get("explanation") or inner.get("clarification_message") or ""
        report.add(
            f"guided:{case['name']}:visible_text",
            bool(str(shown).strip()),
            detail="no explanation or clarification_message for UI",
            group="guided",
        )

        for field_name in CHAT_RESPONSE_UI_FIELDS:
            report.add(
                f"guided:{case['name']}:field:{field_name}",
                field_name in inner,
                detail="missing key (UI may break)",
                group="guided",
            )

    return report


def check_frontend_dist_fresh() -> tuple[bool, str]:
    """Return (ok, detail) — warn when frontend/dist is missing or older than src."""
    dist = frontend_dist_dir()
    src = settings.project_root / "frontend" / "src"
    for name in ("index.html", "app.js", "styles.css"):
        src_path = src / name
        dist_path = dist / name
        if not dist_path.is_file():
            return False, f"missing {dist_path}; run scripts/build-frontend.sh"
        if src_path.is_file() and src_path.stat().st_mtime > dist_path.stat().st_mtime:
            return False, f"{name} in dist is older than src; run scripts/build-frontend.sh"
    return True, ""


def _ensure_mock_llm() -> None:
    """Match tests/conftest.py so offline UI checks use the mock LLM layer."""
    settings.llm_mock_mode = True


def run_checks(
    *,
    groups: set[str] | None = None,
    offline: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 120.0,
) -> CheckReport:
    """Run selected UI check groups: static, api, chat, guided."""
    if offline:
        _ensure_mock_llm()
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

    if "fields" in selected:
        dist = frontend_dist_dir()
        report.merge(check_field_element_contract((dist / "index.html").read_text(encoding="utf-8")))

    needs_getter = "api" in selected or "chat" in selected or "guided" in selected or "fields" in selected
    if needs_getter:
        getter = InProcessGetter() if offline else HttpxGetter(base_url, timeout=timeout)
        try:
            if "api" in selected:
                report.merge(check_api_contract(getter))
            if "fields" in selected:
                report.merge(check_field_blank_whitespace_handling(getter))
                report.merge(check_model_selects_populated(getter))
            if "chat" in selected:
                report.merge(check_chat_smoke(getter))
            if "guided" in selected:
                dist = frontend_dist_dir()
                report.merge(
                    check_guided_ui_contract(
                        (dist / "index.html").read_text(encoding="utf-8"),
                        (dist / "app.js").read_text(encoding="utf-8"),
                        (dist / "styles.css").read_text(encoding="utf-8"),
                    )
                )
                report.merge(check_guided_smoke(getter))
        finally:
            getter.close()

    return report
