import pytest

from medicare_navigator.models.tool_result import ToolStatus
from medicare_navigator.tools.normalize_drug import normalize_drug


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dosage_qualified_lookup_resolves_strength_specific_rxcui():
    """Regression: the plain ingredient-level RxNorm exact match (rxcui.json) resolves
    "lovastatin" to its ingredient RXCUI (6472), which never matches a CMS formulary row —
    those are keyed on the strength-specific clinical-drug RXCUI (e.g. "lovastatin 40 MG
    Oral Tablet" = 197905). Without resolving via /drugs.json when a dosage is given, any
    real dosage-qualified query would be reported as not covered even when it's on the
    formulary. Run with `pytest -m integration` (hits the live RxNorm API)."""
    result = await normalize_drug("lovastatin", "40mg")
    assert result.status == ToolStatus.ok
    assert result.data["selected"]["rxcui"] == "197905"
