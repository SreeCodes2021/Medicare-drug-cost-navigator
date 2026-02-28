from pydantic import BaseModel


class Citation(BaseModel):
    claim: str
    source_id: str
    as_of_date: str
    source_label: str | None = None
    url: str | None = None
