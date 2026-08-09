"""Playwright browser flows — opt-in via RUN_BROWSER_TESTS=1."""

from __future__ import annotations

import os
import threading
import time

import httpx
import pytest
import uvicorn

from medicare_navigator.config import settings
from medicare_navigator.ui_test.checks import BROWSER_FLOW_NAMES
from medicare_navigator.ui_test.browser_flows import run_browser_flow
from tests.spuf_fixture import load_spuf_fixture

pytestmark = pytest.mark.browser

RUN_BROWSER = os.environ.get("RUN_BROWSER_TESTS") == "1"


def pytest_configure(config):
    config.addinivalue_line("markers", "browser: Playwright portal flows")


@pytest.fixture(scope="module")
def browser_base_url():
    """Module-scoped live server with SPUF fixture and mock LLM."""
    settings.llm_mock_mode = True
    data_dir = settings.project_root / ".pytest_browser_data"
    data_dir.mkdir(exist_ok=True)
    duckdb_path = data_dir / "navigator.duckdb"
    load_spuf_fixture(data_dir=data_dir, duckdb_path=duckdb_path)
    settings.data_dir = data_dir
    settings.duckdb_path = duckdb_path

    host, port = "127.0.0.1", 18765
    from medicare_navigator.api.app import app

    config = uvicorn.Config(app, host=host, port=port, log_level="error")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://{host}:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        pytest.fail("Browser test server did not become healthy")

    yield url
    server.should_exit = True


@pytest.mark.skipif(not RUN_BROWSER, reason="Set RUN_BROWSER_TESTS=1 to run Playwright flows")
@pytest.mark.parametrize("flow", BROWSER_FLOW_NAMES)
def test_browser_flow(browser_base_url, flow):
    result = run_browser_flow(flow, base_url=browser_base_url, _isolated=True)
    assert result.ok, result.to_dict()
