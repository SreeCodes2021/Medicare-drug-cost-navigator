from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ToolStatus(str, Enum):
    ok = "ok"
    not_found = "not_found"
    not_covered = "not_covered"
    stale = "stale"
    no_match = "no_match"


class ToolResult(BaseModel, Generic[T]):
    status: ToolStatus
    data: T | None = None
    source_id: str
    as_of_date: str
    message: str | None = None

    @classmethod
    def ok(cls, data: T, source_id: str, as_of_date: str, message: str | None = None) -> "ToolResult[T]":
        return cls(status=ToolStatus.ok, data=data, source_id=source_id, as_of_date=as_of_date, message=message)

    @classmethod
    def failure(
        cls,
        status: ToolStatus,
        source_id: str,
        as_of_date: str,
        message: str | None = None,
    ) -> "ToolResult[T]":
        return cls(status=status, data=None, source_id=source_id, as_of_date=as_of_date, message=message)
