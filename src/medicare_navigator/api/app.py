from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from medicare_navigator.config import settings
from medicare_navigator.models.query import QuerySlots
from medicare_navigator.models.response import ChatResponse
from medicare_navigator.orchestrator.router import orchestrator
from medicare_navigator.storage.repository import PlanRepository

app = FastAPI(title="Medicare Drug Cost Navigator", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class FilterPayload(BaseModel):
    drug: str | None = None
    dosage: str | None = None
    plan_id: str | None = None
    contract_year: int | None = None
    ytd_oop_spend: float | None = None
    pharmacy_channel: str | None = None
    days_supply: int | None = None
    include_alternatives: bool | None = None
    include_cost_trend: bool | None = None


class QueryRequest(BaseModel):
    drug: str | None = None
    dosage: str | None = None
    plan_id: str | None = None
    ytd_oop_spend: float | None = None
    message: str | None = None
    filters: FilterPayload | None = None
    session_id: str | None = None


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    filters: FilterPayload | None = None


def _filters_to_slots(filters: FilterPayload | None, message: str = "") -> QuerySlots | None:
    if not filters:
        return None
    return QuerySlots(**filters.model_dump(exclude_none=True), raw_message=message)


@app.get("/api/health")
async def health():
    from medicare_navigator.llm.client import llm_client

    return {
        "status": "ok",
        "version": "0.1.0",
        "llm_configured": llm_client._has_credentials(),
        "llm_source": llm_client.model_label() if llm_client._has_credentials() else llm_client.fallback_label("navigator"),
        "navigator_mode": settings.navigator_mode,
    }


@app.get("/api/meta/as-of")
async def meta_as_of():
    manifest_path = settings.data_dir / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return {"note": "No manifest found. Run medicare-ingest to seed data."}


@app.get("/api/plans")
async def list_plans(plan_type: str | None = None, state: str | None = None, year: int | None = None):
    repo = PlanRepository()
    return repo.list_plans(plan_type=plan_type, state=state, contract_year=year)


@app.get("/api/disclaimer")
async def get_disclaimer():
    return {"text": settings.disclaimer_text}


@app.post("/api/query")
async def query(req: QueryRequest):
    message = req.message or _build_message_from_fields(req)
    filters = _filters_to_slots(req.filters, message)
    if req.drug and not message:
        message = req.drug
    if req.dosage:
        message = f"{message} {req.dosage}".strip()
    if req.plan_id:
        message = f"{message} plan {req.plan_id}".strip()
    if req.ytd_oop_spend is not None:
        message = f"{message} spent ${req.ytd_oop_spend} YTD".strip()

    response = await orchestrator.run(message=message, filter_slots=filters, session_id=req.session_id)
    return response


@app.post("/api/chat")
async def chat(req: ChatRequest):
    filters = _filters_to_slots(req.filters, req.message)
    response = await orchestrator.run(
        message=req.message, filter_slots=filters, session_id=req.session_id
    )
    from medicare_navigator.session.manager import session_manager

    session = session_manager.get_or_create(response.session_id)
    return ChatResponse(
        session_id=response.session_id or "",
        turn_count=session["turn_count"],
        response=response,
    )


def _build_message_from_fields(req: QueryRequest) -> str:
    parts = []
    if req.drug:
        parts.append(req.drug)
    if req.dosage:
        parts.append(req.dosage)
    if req.plan_id:
        parts.append(f"plan {req.plan_id}")
    return " ".join(parts)


_frontend = settings.project_root / "frontend" / "dist"
if _frontend.exists():
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
