from enum import Enum

from pydantic import BaseModel, Field

from medicare_navigator.models.citation import Citation


class BenefitPhase(str, Enum):
    DEDUCTIBLE = "deductible"
    INITIAL_COVERAGE = "initial_coverage"
    CATASTROPHIC = "catastrophic"


class CostShareInfo(BaseModel):
    tier: int | None = None
    copay: float | None = None
    coinsurance_pct: float | None = None
    cost_type: str = "copay"


class FormularyResult(BaseModel):
    plan_key: str
    plan_name: str
    tier: int | None = None
    cost_share: CostShareInfo | None = None
    benefit_phase: str | None = None
    ytd_oop_spend: float
    oop_threshold: float
    deductible: float
    covered: bool = True
    ytd_oop_spend_assumed: bool = True
    supply_estimate: "SupplyEstimate | None" = None


class SupplyScenario(BaseModel):
    label: str
    estimated_patient_cost: float
    formula_description: str
    fills: int | None = None
    quantity: int | None = None


class SupplyEstimate(BaseModel):
    estimated_patient_cost: float | None = None
    calculation_method: str
    formula_description: str
    fills: int | None = None
    quantity: int | None = None
    assumptions: list[str] = Field(default_factory=list)
    scenarios: list[SupplyScenario] | None = None


class CostTrendPoint(BaseModel):
    year: int
    total_spend: float
    avg_unit_cost: float | None = None


class AlternativesResult(BaseModel):
    drug_name: str
    rxcui: str
    te_code: str | None = None
    equivalent: bool = True


class QueryResponse(BaseModel):
    query_id: str
    session_id: str | None = None
    status: str = "ok"
    drug_name: str | None = None
    rxcui: str | None = None
    formulary: FormularyResult | None = None
    cost_trend: list[CostTrendPoint] = Field(default_factory=list)
    alternatives: list[AlternativesResult] = Field(default_factory=list)
    explanation: str = ""
    citations: list[Citation] = Field(default_factory=list)
    disclaimer: str = ""
    data_as_of: dict[str, str] = Field(default_factory=dict)
    tools_invoked: list[str] = Field(default_factory=list)
    agents_invoked: list[str] = Field(default_factory=list)
    clarification_message: str | None = None
    tool_statuses: dict[str, str] = Field(default_factory=dict)
    response_source: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    turn_count: int
    response: QueryResponse
