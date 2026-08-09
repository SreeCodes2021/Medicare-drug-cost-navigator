import pytest

from medicare_navigator.models.tool_result import ToolStatus
from medicare_navigator.tools.normalize_drug import canonicalize_drug_name, normalize_drug


def test_canonicalize_drug_name_spanish_alias():
    assert canonicalize_drug_name("metformina") == "metformin"


def test_canonicalize_drug_name_fuzzy_typo():
    assert canonicalize_drug_name("metfomrin") == "metformin"


def test_canonicalize_drug_name_repeated_tokens():
    repeated = " ".join(["metformin"] * 200)
    assert canonicalize_drug_name(repeated) == "metformin"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dosage_qualified_lookup_resolves_strength_specific_rxcui_lisinopril():
    """CMS formulary rows use strength-specific RXCUI 314076, not ingredient 29046."""
    result = await normalize_drug("lisinopril", "10mg")
    assert result.status == ToolStatus.ok
    assert result.data["selected"]["rxcui"] == "314076"


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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_spanish_metformina_resolves_strength_specific_rxcui():
    result = await normalize_drug("metformina", "500mg")
    assert result.status == ToolStatus.ok
    assert result.data["selected"]["rxcui"] == "861007"


@pytest.mark.asyncio
async def test_normalize_drug_without_dosage_still_resolves_ingredient():
    result = await normalize_drug("metformin")
    assert result.status == ToolStatus.ok
    assert result.data["selected"]["drug_name"] == "metformin"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_typo_metfomrin_resolves_with_dosage():
    result = await normalize_drug("metfomrin", "500mg")
    assert result.status == ToolStatus.ok
    assert result.data["selected"]["rxcui"] == "861007"
