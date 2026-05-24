from __future__ import annotations


class LLMNotConfiguredError(RuntimeError):
    """Raised when chat/intake is invoked without API credentials or mock mode."""


class LLMRequestError(RuntimeError):
    """Raised when an LLM API call fails after retries."""
