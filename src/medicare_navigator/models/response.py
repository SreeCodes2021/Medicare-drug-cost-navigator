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


class QueryResponse(BaseModel):
    query_id: str
    session_id: str | None = None
    status: str = "ok"
    drug_name: str | None = None
    rxcui: str | None = None
    estimate: DrugCostEstimate | None = None
    explanation: str = ""
    citations: list[Citation] = Field(default_factory=list)
    disclaimer: str = ""
    data_as_of: dict[str, str] = Field(default_factory=dict)
    tools_invoked: list[str] = Field(default_factory=list)
    tool_statuses: dict[str, str] = Field(default_factory=dict)
    clarification_message: str | None = None
    response_source: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    turn_count: int
    response: QueryResponse
