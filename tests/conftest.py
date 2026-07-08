import subprocess

import pytest

from medicare_navigator.config import settings
from tests.spuf_fixture import patch_settings


@pytest.fixture(scope="session", autouse=True)
def ensure_frontend_dist():
    dist_index = settings.project_root / "frontend" / "dist" / "index.html"
    if dist_index.exists():
        return
    script = settings.project_root / "scripts" / "build-frontend.sh"
    subprocess.run([str(script)], check=True, cwd=settings.project_root)


@pytest.fixture(autouse=True)
def use_mock_llm(monkeypatch):
    """Use offline mock LLM responses instead of live API calls."""
    monkeypatch.setattr(settings, "llm_mock_mode", True)


@pytest.fixture
def spuf_db(tmp_path, monkeypatch):
    """DuckDB loaded with offline SPUF fixture (FL test plans)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    return data_dir
