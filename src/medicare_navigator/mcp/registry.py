from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from medicare_navigator.ingestion.manifest import get_as_of, get_source_id
from medicare_navigator.mcp.schemas import TOOL_SCHEMAS
from medicare_navigator.models.tool_result import ToolResult
from medicare_navigator.storage.repository import PlanRepository
from medicare_navigator.tools.alternatives import alternatives_finder
from medicare_navigator.tools.cost_trend import cost_trend_lookup
from medicare_navigator.tools.formulary_benefit import formulary_benefit_lookup
from medicare_navigator.tools.lookup_plan import lookup_plan
from medicare_navigator.tools.normalize_drug import normalize_drug
from medicare_navigator.tools.policy_retrieval import policy_retrieval

SOURCE_ID_FALLBACK = "cms_spuf_2026_q1_demo"
AS_OF_FALLBACK = "2026-01-15"


def _spuf_source_id() -> str:
    return get_source_id("spuf", SOURCE_ID_FALLBACK)


def _spuf_as_of() -> str:
    return get_as_of("spuf", AS_OF_FALLBACK)


def _serialize_tool_result(result: ToolResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": result.status.value,
        "source_id": result.source_id,
        "as_of_date": result.as_of_date,
        "message": result.message,
    }
    if result.data is None:
        payload["data"] = None
    elif isinstance(result.data, BaseModel):
        payload["data"] = result.data.model_dump()
    elif isinstance(result.data, list):
        payload["data"] = [
            item.model_dump() if isinstance(item, BaseModel) else item for item in result.data
        ]
    else:
        payload["data"] = result.data
    return payload


async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    args = dict(arguments or {})

    if name == "normalize_drug":
        result = await normalize_drug(args.get("drug_name", ""), args.get("dosage"))
    elif name == "lookup_plan":
        result = lookup_plan(
            plan_key=args.get("plan_key"),
            search_text=args.get("search_text"),
        )
    elif name == "list_plans":
        repo = PlanRepository()
        plans = repo.list_plans(
            plan_type=args.get("plan_type"),
            state=args.get("state"),
            contract_year=args.get("contract_year"),
        )
        result = ToolResult.ok(plans, source_id=_spuf_source_id(), as_of_date=_spuf_as_of())
    elif name == "formulary_benefit_lookup":
        result = formulary_benefit_lookup(
            plan_key=args["plan_key"],
            ndc=args["ndc"],
            ytd_oop_spend=float(args.get("ytd_oop_spend", 0)),
            contract_year=int(args.get("contract_year", 2026)),
            ytd_oop_spend_provided=bool(args.get("ytd_oop_spend_provided", False)),
            quantity=args.get("quantity"),
            fills=args.get("fills"),
            days_supply=args.get("days_supply", 30),
            pharmacy_channel=args.get("pharmacy_channel", "preferred_retail"),
        )
    elif name == "cost_trend_lookup":
        result = cost_trend_lookup(args["rxcui"])
    elif name == "alternatives_finder":
        result = alternatives_finder(args["rxcui"])
    elif name == "policy_retrieval":
        result = policy_retrieval(args.get("query_text", ""))
    else:
        return {
            "status": "not_found",
            "source_id": "navigator",
            "as_of_date": _spuf_as_of(),
            "message": f"Unknown tool: {name}",
            "data": None,
        }

    return _serialize_tool_result(result)


def tool_names() -> list[str]:
    return [schema["name"] for schema in TOOL_SCHEMAS]


def tool_result_json(result: dict[str, Any]) -> str:
    return json.dumps(result, default=str)
