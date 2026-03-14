import pytest

from medicare_navigator.config import settings
from tests.spuf_fixture import patch_settings


@pytest.fixture(autouse=True)
def use_deterministic_llm(monkeypatch):
    """Force rule-based LLM fallback so tests do not require network access."""
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")


@pytest.fixture
def spuf_db(tmp_path, monkeypatch):
    """DuckDB loaded with offline SPUF fixture (FL + TX test plans)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    patch_settings(monkeypatch, data_dir)
    return data_dir
