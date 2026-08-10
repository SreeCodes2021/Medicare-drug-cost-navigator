from pydantic import BaseModel, Field

from medicare_navigator.models.citation import Citation


class DrugCostEstimate(BaseModel):
    plan_key: str
    plan_name: str
    drug_name: str
    rxcui: str | None = None
    tiers_matched: list[int] = Field(default_factory=list)
    matched_ndc_count: int = 0
    same_tier: bool = True
    days_supply: int
    benefit_phase: str | None = None  # "pre_deductible" | "initial_coverage"
    cost_low: float | None = None
    cost_high: float | None = None
    caveats: list[str] = Field(default_factory=list)
    quantity_limit_blocked: bool = False
    max_allowed_days_supply: int | None = None
    covered: bool = True


class ChannelCost(BaseModel):
    """Per-pharmacy-channel cost; null cost_low/cost_high means NA."""

    cost_low: float | None = None
    cost_high: float | None = None
    coinsurance: bool = False
    plan_copay: float | None = None
    plan_coinsurance_pct: float | None = None
    applied_copay: float | None = None
    applied_coinsurance_pct: float | None = None


class MultiChannelDrugCostEstimate(BaseModel):
    plan_key: str
    plan_name: str
    drug_name: str | None = None
    dosage: str | None = None
    rxcui: str | None = None
    covered: bool | None = None
    days_supply: int
    ytd_oop_spend: float
    deductible: float | None = None
    tier: int | None = None
    tiers_matched: list[int] = Field(default_factory=list)
    ded_applies_yn: str = "NA"
    benefit_phase: str | None = None
    effective_phase: str | None = None
    channels: dict[str, ChannelCost] = Field(default_factory=dict)
    matched_ndc_count: int = 0
    same_tier: bool = True
    caveats: list[str] = Field(default_factory=list)
    quantity_limit_blocked: bool = False
    max_allowed_days_supply: int | None = None
    annual_oop_cap: float | None = None
    remaining_oop_headroom: float | None = None
    annual_budget_cost_low: float | None = None
    annual_budget_cost_high: float | None = None
    remaining_year_days: int | None = None
    remaining_year_fills: int | None = None
    remaining_year_budget_cost_low: float | None = None
    remaining_year_budget_cost_high: float | None = None


class EstimateApiResponse(BaseModel):
    status: str
    message: str | None = None
    data: MultiChannelDrugCostEstimate | None = None
    source_id: str = ""
    as_of_date: str = ""


class BatchEstimateItem(BaseModel):
    drug: str
    data: MultiChannelDrugCostEstimate | None = None
    status: str
    message: str | None = None


class BatchEstimateApiResponse(BaseModel):
    status: str
    items: list[BatchEstimateItem] = Field(default_factory=list)
    combined_total_low: float | None = None
    combined_total_high: float | None = None
    caveat: str | None = None


class PlanComparisonItem(BaseModel):
    plan_id: str
    data: MultiChannelDrugCostEstimate | None = None
    status: str
    message: str | None = None


class PlanComparisonApiResponse(BaseModel):
    status: str
    items: list[PlanComparisonItem] = Field(default_factory=list)
    disclaimer: str = (
        "Pharmacy fill cost-share only — plan premiums are not included in this comparison. "
        "This is not a recommendation to switch plans."
    )


class LlmUsage(BaseModel):
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


class QueryResponse(BaseModel):
    query_id: str
    session_id: str | None = None
    status: str = "ok"
    drug_name: str | None = None
    rxcui: str | None = None
    estimate: DrugCostEstimate | None = None
    channel_estimate: MultiChannelDrugCostEstimate | None = None
    channel_estimates: list[MultiChannelDrugCostEstimate] = Field(default_factory=list)
    explanation: str = ""
    citations: list[Citation] = Field(default_factory=list)
    disclaimer: str = ""
    data_as_of: dict[str, str] = Field(default_factory=dict)
    tools_invoked: list[str] = Field(default_factory=list)
    tool_statuses: dict[str, str] = Field(default_factory=dict)
    clarification_message: str | None = None
    response_source: str | None = None
    llm_usage: LlmUsage | None = None


class ChatResponse(BaseModel):
    session_id: str
    turn_count: int
    response: QueryResponse
