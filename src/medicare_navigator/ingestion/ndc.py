"""NDC normalization helpers for CMS SPUF and RxNorm crosswalk."""

from __future__ import annotations

import re


def normalize_ndc(ndc: str) -> str:
    """Return 11-digit NDC with no dashes or spaces."""
    digits = re.sub(r"\D", "", ndc or "")
    if len(digits) != 11:
        raise ValueError(f"NDC must be 11 digits after normalization, got {len(digits)}: {ndc!r}")
    return digits


def format_ndc_display(ndc: str) -> str:
    """Format 11-digit NDC as labeler-product-package (5-4-2)."""
    normalized = normalize_ndc(ndc)
    return f"{normalized[:5]}-{normalized[5:9]}-{normalized[9:]}"


def ndc_matches(a: str, b: str) -> bool:
    """Compare two NDC strings regardless of dash formatting."""
    try:
        return normalize_ndc(a) == normalize_ndc(b)
    except ValueError:
        return False
