import pytest

from medicare_navigator.config import settings


@pytest.fixture(autouse=True)
def use_deterministic_llm(monkeypatch):
    """Force rule-based LLM fallback so tests do not require network access."""
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
