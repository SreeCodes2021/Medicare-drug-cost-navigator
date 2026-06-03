from pydantic import BaseModel


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
    days_supply: int | None = 30
    raw_message: str = ""
