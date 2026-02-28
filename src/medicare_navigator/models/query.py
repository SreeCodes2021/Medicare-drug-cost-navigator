from pydantic import BaseModel, Field


class Citation(BaseModel):
    claim: str
    source_id: str
    as_of_date: str
    source_label: str | None = None
    url: str | None = None


class QuerySlots(BaseModel):
    drug: str | None = None
    dosage: str | None = None
    plan_id: str | None = None
    contract_year: int | None = None
    ytd_oop_spend: float | None = None
    pharmacy_channel: str | None = "preferred_retail"
    days_supply: int | None = 30
    include_alternatives: bool | None = True
    include_cost_trend: bool | None = True
    raw_message: str = ""
    intents: list[str] = Field(default_factory=list)


class ParsedQuery(BaseModel):
    drug_name: str
    rxcui: str | None = None
    ndc: str | None = None
    dosage: str | None = None
    plan_key: str | None = None
    contract_id: str | None = None
    plan_segment_id: str | None = None
    contract_year: int = 2026
    ytd_oop_spend: float = 0.0
    pharmacy_channel: str = "preferred_retail"
    days_supply: int = 30
    include_alternatives: bool = True
    include_cost_trend: bool = True
    intents: list[str] = Field(default_factory=lambda: ["tier_lookup"])
    raw_message: str = ""


class IntakeResult(BaseModel):
    status: str  # complete | needs_clarification | not_found
    slots: QuerySlots
    parsed_query: ParsedQuery | None = None
    clarification_message: str | None = None
    missing_slots: list[str] = Field(default_factory=list)
    follow_up_type: str | None = None
    slots_unchanged: bool = False
