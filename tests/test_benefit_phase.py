from medicare_navigator.tools.normalize_drug import compute_benefit_phase


def test_compute_benefit_phase_catastrophic_at_annual_cap():
    assert compute_benefit_phase(2100, 615, contract_year=2026) == "catastrophic"
    assert compute_benefit_phase(2200, 615, contract_year=2026) == "catastrophic"
    assert compute_benefit_phase(2000, 615, contract_year=2025) == "catastrophic"


def test_compute_benefit_phase_initial_below_cap():
    assert compute_benefit_phase(700, 615, contract_year=2026) == "initial_coverage"
