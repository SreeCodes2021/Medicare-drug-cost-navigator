"""Playwright browser flows for Medicare Navigator portal surfaces."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from medicare_navigator.ui_test.checks import (
    BROWSER_FLOW_NAMES,
    check_frontend_dist_fresh,
    DEFAULT_BASE_URL,
)

# Fixture plans (offline pytest). Override for live CMS data:
#   UI_TEST_PLAN_PDP=S5921-400 UI_TEST_PLAN_MAPD=H0270-001
PLAN_FL_PDP = os.environ.get("UI_TEST_PLAN_PDP", "S9999-001")
PLAN_FL_MAPD = os.environ.get("UI_TEST_PLAN_MAPD", "H8888-001")
DEFAULT_TIMEOUT_MS = 90_000


@dataclass
class FlowCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class FlowResult:
    flow: str
    ok: bool
    checks: list[FlowCheck] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    detail: str = ""
    screenshot_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "flow": self.flow,
            "detail": self.detail,
            "screenshot_path": self.screenshot_path,
            "console_errors": self.console_errors,
            "checks": [c.__dict__ for c in self.checks],
        }


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install -e '.[browser]' && playwright install chromium"
        ) from exc
    return sync_playwright


def _wait_for_plans(page, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> None:
    """Wait until GET /api/plans returns a non-empty list."""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        count = page.evaluate(
            """async () => {
                const res = await fetch('/api/plans');
                if (!res.ok) return 0;
                const plans = await res.json();
                return Array.isArray(plans) ? plans.length : 0;
            }"""
        )
        if count > 0:
            return
        time.sleep(0.25)
    raise TimeoutError("Plan list did not load within timeout")


def _select_plan_combobox(page, input_id: str, listbox_id: str, plan_key: str) -> None:
    inp = page.locator(f"#{input_id}")
    inp.click()
    inp.fill(plan_key)
    page.wait_for_selector(f"#{listbox_id} .plan-option", timeout=DEFAULT_TIMEOUT_MS)
    option = page.locator(f"#{listbox_id} .plan-option").filter(has_text=f"({plan_key})")
    option.first.click()


def _assistant_visible(page, container_id: str) -> bool:
    return page.locator(f"#{container_id} .message.assistant").count() > 0


def _results_ready(page, content_id: str) -> bool:
    content = page.locator(f"#{content_id}")
    if content.locator(".placeholder").count() > 0:
        return False
    text = content.inner_text().strip()
    return len(text) > 20


def _guided_results_ok(page, content_id: str, messages_id: str) -> bool:
    if _results_ready(page, content_id):
        return True
    assistant = page.locator(f"#{messages_id} .message.assistant")
    if assistant.count() == 0:
        return False
    return len(assistant.first.inner_text().strip()) > 30


def run_chat_flow(base_url: str = DEFAULT_BASE_URL, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> FlowResult:
    message = f"What's the cost for metformin 500mg on plan {PLAN_FL_PDP}?"
    checks: list[FlowCheck] = []
    console_errors: list[str] = []

    with _require_playwright()() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.goto(base_url.rstrip("/"), wait_until="networkidle", timeout=timeout_ms)

        page.locator("#chat-input").fill(message)
        page.locator("#send-btn").click()

        page.wait_for_selector("#chat-messages .message.assistant", timeout=timeout_ms)
        page.locator("#loading").wait_for(state="hidden", timeout=timeout_ms)

        turn_text = page.locator("#turn-counter").inner_text()
        checks.append(
            FlowCheck("turn_counter", "1/5" in turn_text, detail=turn_text)
        )
        checks.append(
            FlowCheck("assistant_message", _assistant_visible(page, "chat-messages"))
        )
        checks.append(
            FlowCheck("results_content", _results_ready(page, "results-content"))
        )

        browser.close()

    ok = all(c.passed for c in checks)
    return FlowResult(flow="chat", ok=ok, checks=checks, console_errors=console_errors)


def run_guided_single_flow(
    base_url: str = DEFAULT_BASE_URL, *, timeout_ms: int = DEFAULT_TIMEOUT_MS
) -> FlowResult:
    checks: list[FlowCheck] = []
    console_errors: list[str] = []

    with _require_playwright()() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.goto(base_url.rstrip("/"), wait_until="networkidle", timeout=timeout_ms)

        _wait_for_plans(page, timeout_ms)
        page.locator("#mode-tab-guided").click()
        page.locator("#filter-drug").fill("metformin")
        page.locator("#filter-dosage").fill("500mg")
        _select_plan_combobox(page, "filter-plan-input", "filter-plan-listbox", PLAN_FL_PDP)
        page.locator("#guided-submit").click()

        page.wait_for_selector("#guided-chat-messages .message.assistant", timeout=timeout_ms)
        page.locator("#guided-loading").wait_for(state="hidden", timeout=timeout_ms)

        turn_text = page.locator("#guided-turn-counter").inner_text()
        checks.append(FlowCheck("guided_turn_counter", "1/5" in turn_text, detail=turn_text))
        checks.append(FlowCheck("guided_assistant", _assistant_visible(page, "guided-chat-messages")))
        checks.append(
            FlowCheck(
                "guided_results",
                _guided_results_ok(page, "guided-results-content", "guided-chat-messages"),
            )
        )
        followup_disabled = page.locator("#guided-chat-input").is_disabled()
        checks.append(FlowCheck("followup_enabled", not followup_disabled))

        browser.close()

    ok = all(c.passed for c in checks)
    return FlowResult(flow="guided-single", ok=ok, checks=checks, console_errors=console_errors)


def run_guided_multi_flow(
    base_url: str = DEFAULT_BASE_URL, *, timeout_ms: int = DEFAULT_TIMEOUT_MS
) -> FlowResult:
    checks: list[FlowCheck] = []
    console_errors: list[str] = []

    with _require_playwright()() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.goto(base_url.rstrip("/"), wait_until="networkidle", timeout=timeout_ms)

        _wait_for_plans(page, timeout_ms)
        page.locator("#mode-tab-guided").click()
        page.locator("#guided-mode-multidrug").click()
        _select_plan_combobox(page, "md-plan-input", "md-plan-listbox", PLAN_FL_PDP)

        page.locator("#md-drug-1").fill("metformin")
        page.locator("#md-dosage-1").fill("500mg")
        page.locator("#multidrug-add-row").click()
        page.locator("#md-drug-2").fill("januvia")
        page.locator("#md-dosage-2").fill("100mg")
        page.locator("#multidrug-submit").click()

        page.wait_for_selector("#guided-chat-messages .message.assistant", timeout=timeout_ms)
        page.locator("#guided-loading").wait_for(state="hidden", timeout=timeout_ms)

        checks.append(
            FlowCheck("guided_assistant", _assistant_visible(page, "guided-chat-messages"))
        )
        checks.append(
            FlowCheck(
                "guided_results",
                _guided_results_ok(page, "guided-results-content", "guided-chat-messages"),
            )
        )

        browser.close()

    ok = all(c.passed for c in checks)
    return FlowResult(flow="guided-multi", ok=ok, checks=checks, console_errors=console_errors)


def run_guided_compare_plan_flow(
    base_url: str = DEFAULT_BASE_URL, *, timeout_ms: int = DEFAULT_TIMEOUT_MS
) -> FlowResult:
    checks: list[FlowCheck] = []
    console_errors: list[str] = []

    with _require_playwright()() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.goto(base_url.rstrip("/"), wait_until="networkidle", timeout=timeout_ms)

        _wait_for_plans(page, timeout_ms)
        page.locator("#mode-tab-guided").click()
        page.locator("#guided-mode-compareplans").click()
        page.locator("#cp-drug").fill("metformin")
        page.locator("#cp-dosage").fill("500mg")
        _select_plan_combobox(page, "cp-plan-input-1", "cp-plan-listbox-1", PLAN_FL_PDP)
        _select_plan_combobox(page, "cp-plan-input-2", "cp-plan-listbox-2", PLAN_FL_MAPD)
        page.locator("#compareplans-submit").click()

        page.wait_for_selector("#guided-chat-messages .message.assistant", timeout=timeout_ms)
        page.locator("#guided-loading").wait_for(state="hidden", timeout=timeout_ms)

        checks.append(
            FlowCheck("guided_assistant", _assistant_visible(page, "guided-chat-messages"))
        )
        checks.append(
            FlowCheck(
                "guided_results",
                _guided_results_ok(page, "guided-results-content", "guided-chat-messages"),
            )
        )

        browser.close()

    ok = all(c.passed for c in checks)
    return FlowResult(flow="guided-compare-plan", ok=ok, checks=checks, console_errors=console_errors)


_FLOW_RUNNERS: dict[str, Callable[..., FlowResult]] = {
    "chat": run_chat_flow,
    "guided-single": run_guided_single_flow,
    "guided-multi": run_guided_multi_flow,
    "guided-compare-plan": run_guided_compare_plan_flow,
}


def run_browser_flow(
    flow: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    _isolated: bool = False,
) -> FlowResult:
    if flow not in _FLOW_RUNNERS:
        raise ValueError(f"Unknown flow {flow!r}; expected one of {BROWSER_FLOW_NAMES}")

    fresh, detail = check_frontend_dist_fresh()
    if not fresh:
        return FlowResult(flow=flow, ok=False, detail=detail)

    runner = _FLOW_RUNNERS[flow]
    if not _isolated:
        return runner(base_url, timeout_ms=timeout_ms)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runner, base_url, timeout_ms=timeout_ms)
        return future.result()
