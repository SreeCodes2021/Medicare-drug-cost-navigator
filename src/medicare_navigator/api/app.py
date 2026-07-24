from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from medicare_navigator.config import settings
from medicare_navigator.llm.errors import LLMNotConfiguredError, LLMRequestError
from medicare_navigator.models.query import QuerySlots
from medicare_navigator.models.response import ChatResponse, EstimateApiResponse, MultiChannelDrugCostEstimate
from medicare_navigator.orchestrator.router import orchestrator
from medicare_navigator.storage.repository import PlanRepository


@asynccontextmanager
async def lifespan(_app: FastAPI):
    import logging

    from medicare_navigator.ingestion.schema import ensure_schema

    ensure_schema()
    log = logging.getLogger("uvicorn.error")
    for provider, status in settings.llm_provider_status().items():
        if status == "empty_in_env_file":
            log.warning(settings.llm_configuration_hint(provider))
        elif status == "missing" and provider == settings.llm_provider.lower():
            log.warning(settings.llm_configuration_hint(provider))
    yield


app = FastAPI(title="Medicare Drug Cost Navigator", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class _NoCacheFrontendMiddleware(BaseHTTPMiddleware):
    """Prevent stale index.html/CSS/JS during local dev (browser 304 caching)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response


app.add_middleware(_NoCacheFrontendMiddleware)


class FilterPayload(BaseModel):
    drug: str | None = None
    dosage: str | None = None
    plan_id: str | None = None
    contract_year: int | None = None
    ytd_oop_spend: float | None = None
    days_supply: int | None = None


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
    model: str | None = None


class EstimateRequest(BaseModel):
    plan_id: str
    drug: str
    dosage: str | None = None
    days_supply: int = 30
    ytd_oop_spend: float = 0.0


def _filters_to_slots(filters: FilterPayload | None, message: str = "") -> QuerySlots | None:
    if not filters:
        return None
    return QuerySlots(**filters.model_dump(exclude_none=True), raw_message=message)


@app.get("/api/health")
async def health():
    from medicare_navigator.ingestion.manifest import data_freshness_summary
    from medicare_navigator.llm.client import llm_client

    freshness = data_freshness_summary()
    llm_ok = llm_client.is_available()
    provider_status = settings.llm_provider_status()
    body = {
        "status": "ok" if llm_ok else "degraded",
        "version": "0.1.0",
        "llm_configured": llm_ok,
        "llm_source": llm_client.model_label(),
        "llm_providers": provider_status,
        "env_file": str(settings.env_file),
        **freshness,
    }
    if not llm_ok:
        body["error"] = (
            "LLM API key is not configured. Set OPENAI_API_KEY and/or ANTHROPIC_API_KEY "
            "for the models you want to use."
        )
        return JSONResponse(status_code=503, content=body)
    return body


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


@app.post("/api/estimate", response_model=EstimateApiResponse)
async def estimate_costs(req: EstimateRequest):
    from medicare_navigator.tools.estimate_drug_cost import estimate_drug_cost_all_channels

    if not req.plan_id.strip():
        raise HTTPException(status_code=400, detail="plan_id is required")
    if not req.drug.strip():
        raise HTTPException(status_code=400, detail="drug is required")

    result = await estimate_drug_cost_all_channels(
        plan_key=req.plan_id.strip(),
        drug_name=req.drug.strip(),
        dosage=req.dosage.strip() if req.dosage else None,
        days_supply=req.days_supply,
        ytd_oop_spend=req.ytd_oop_spend,
    )
    data: MultiChannelDrugCostEstimate | None = None
    if isinstance(result.data, MultiChannelDrugCostEstimate):
        data = result.data
    return EstimateApiResponse(
        status=result.status.value,
        message=result.message,
        data=data,
        source_id=result.source_id,
        as_of_date=result.as_of_date,
    )


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

    try:
        response = await orchestrator.run(message=message, filter_slots=filters, session_id=req.session_id)
    except LLMNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return response


@app.get("/api/models")
async def list_models():
    from medicare_navigator.llm.models import DEFAULT_LLM_MODEL, list_available_models

    return {"default": DEFAULT_LLM_MODEL, "models": list_available_models()}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    filters = _filters_to_slots(req.filters, req.message)
    try:
        response = await orchestrator.run(
            message=req.message,
            filter_slots=filters,
            session_id=req.session_id,
            llm_model=req.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
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
    _no_cache = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}

    @app.get("/", include_in_schema=False)
    async def serve_index():
        return FileResponse(_frontend / "index.html", media_type="text/html", headers=_no_cache)

    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
