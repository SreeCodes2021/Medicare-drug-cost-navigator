from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from medicare_navigator.analytics.collector import collector
from medicare_navigator.config import settings
from medicare_navigator.llm.errors import LLMNotConfiguredError, LLMRequestError
from medicare_navigator.models.query import QuerySlots
from medicare_navigator.models.response import (
    BatchEstimateApiResponse,
    BatchEstimateItem,
    ChatResponse,
    EstimateApiResponse,
    MultiChannelDrugCostEstimate,
    PlanComparisonApiResponse,
    PlanComparisonItem,
)
from medicare_navigator.orchestrator.router import orchestrator
from medicare_navigator.storage.repository import PlanRepository
from medicare_navigator.tools.batch_estimate import (
    MAX_BATCH_DRUGS,
    MAX_COMPARE_PLANS,
    BatchEstimateRequest,
    run_batch_estimates,
)


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

    flush_task = None
    if settings.analytics_enabled:
        from medicare_navigator.analytics.flush import flush_loop

        flush_task = asyncio.create_task(flush_loop(settings.analytics_flush_interval_seconds))
    yield
    if flush_task is not None:
        flush_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await flush_task


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
    region: str | None = None


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    filters: FilterPayload | None = None
    model: str | None = None
    timezone: str | None = None
    region: str | None = None
    mode: str | None = None


class EstimateRequest(BaseModel):
    plan_id: str
    drug: str
    dosage: str | None = None
    days_supply: int = 30
    ytd_oop_spend: float = 0.0


class BatchEstimateDrugItem(BaseModel):
    drug: str
    dosage: str | None = None


class BatchEstimateRequestPayload(BaseModel):
    plan_id: str
    items: list[BatchEstimateDrugItem]
    days_supply: int = 30
    ytd_oop_spend: float = 0.0


class ComparePlansRequestPayload(BaseModel):
    drug: str
    dosage: str | None = None
    plan_ids: list[str]
    days_supply: int = 30
    ytd_oop_spend: float = 0.0


def _estimate_cost_bounds(
    data: MultiChannelDrugCostEstimate,
) -> tuple[float | None, float | None]:
    lows: list[float] = []
    highs: list[float] = []
    for channel in data.channels.values():
        if channel.cost_low is not None:
            lows.append(channel.cost_low)
        if channel.cost_high is not None:
            highs.append(channel.cost_high)
        elif channel.cost_low is not None:
            highs.append(channel.cost_low)
    if not lows:
        return None, None
    return min(lows), max(highs) if highs else max(lows)


def _filters_to_slots(filters: FilterPayload | None, message: str = "") -> QuerySlots | None:
    if not filters:
        return None
    return QuerySlots(**filters.model_dump(exclude_none=True), raw_message=message)


def _valid_state_code(candidate: str) -> str | None:
    candidate = (candidate or "").strip().upper()
    if len(candidate) == 2 and candidate.isalpha():
        return candidate
    return None


# Interaction modes for analytics only — never affects routing/response logic.
_VALID_MODES = frozenset({"chat", "guided_single", "guided_compare_drug", "guided_compare_plan"})


def _resolve_mode(mode: str | None) -> str:
    candidate = (mode or "").strip()
    return candidate if candidate in _VALID_MODES else "chat"


# CMS plan_key shape: contract_id ("H"/"S"/"R" + 4 digits) + "-" + 3-digit plan id,
# e.g. "S9999-001", "H8888-001" — see plan_key = f"{contract_id}-{plan_id}" in
# ingestion/spuf.py. Used only to spot a plan ID typed directly in chat text (not
# picked via the plan combobox) so its state can be resolved for analytics.
_PLAN_KEY_IN_TEXT_RE = re.compile(r"\b([A-Z]\d{4}-\d{3})\b")


async def _plan_state(plan_id: str) -> str | None:
    plan = await asyncio.to_thread(PlanRepository().get_plan, plan_id.strip())
    if not plan:
        return None
    return _valid_state_code(plan.get("state") or "")


async def _resolve_region(region: str | None, plan_id: str | None, message: str = "") -> str:
    """User-selected 2-letter state code for analytics only — never used to filter
    or adjust cost estimates. Falls back, in order, to: the state of a picked
    plan_id (plan combobox / guided form), then the state of a plan ID typed
    directly in the message text. Anything else — no state, no resolvable plan,
    or a malformed/free-text value — collapses to 'unknown' so it can't pollute
    the usage_hourly bucket key."""
    direct = _valid_state_code(region or "")
    if direct:
        return direct
    if plan_id and plan_id.strip():
        state = await _plan_state(plan_id)
        if state:
            return state
    match = _PLAN_KEY_IN_TEXT_RE.search(message or "")
    if match:
        state = await _plan_state(match.group(1))
        if state:
            return state
    return "unknown"


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


@app.get("/api/data-releases")
async def list_data_releases():
    """CMS SPUF data releases available locally (YYYY-Qn), for guided-form picker."""
    from medicare_navigator.ingestion.manifest import list_data_releases as releases

    return {"releases": releases()}


@app.get("/api/data-release")
async def get_data_release():
    """Active CMS SPUF data release (YYYY-Qn from ingest date)."""
    from medicare_navigator.ingestion.manifest import get_data_release as release

    return {"release": release()}


@app.get("/api/plans")
async def list_plans(plan_type: str | None = None, state: str | None = None, year: int | None = None):
    repo = PlanRepository()
    return repo.list_plans(plan_type=plan_type, state=state, contract_year=year)


@app.get("/api/states")
async def list_states():
    """States with at least one ingested plan — drives the state picker UI."""
    return {"states": PlanRepository().list_states()}


@app.get("/api/zip-lookup")
async def zip_lookup(zip: str):
    """Best-effort zip -> state (static USPS ZIP3 table). Discovery/UX only —
    never used to filter or adjust cost estimates."""
    from medicare_navigator.tools.zip_lookup import zip_to_state

    return {"zip": zip, "state": zip_to_state(zip)}


@app.get("/api/drugs")
async def list_drugs(q: str | None = None, plan_id: str | None = None):
    """Drug names for the guided-form picker — discovery/UX only."""
    from medicare_navigator.tools.drug_lookup import search_drugs

    plan_key = (plan_id or "").strip() or None
    return {"drugs": await search_drugs(q, plan_id=plan_key)}


@app.get("/api/drug-dosages")
async def list_drug_dosages(drug: str, plan_id: str | None = None):
    """Dosage strengths available for a drug (RxNorm) — discovery/UX only."""
    from medicare_navigator.tools.drug_lookup import list_drug_dosages as lookup_dosages

    name = drug.strip()
    if not name:
        raise HTTPException(status_code=400, detail="drug is required")
    plan_key = (plan_id or "").strip() or None
    return {"drug": name, "dosages": await lookup_dosages(name, plan_id=plan_key)}


@app.get("/api/disclaimer")
async def get_disclaimer():
    return {"text": settings.disclaimer_text}


@app.get("/api/admin/usage")
async def admin_usage(
    x_admin_token: str | None = Header(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
):
    """Aggregate-only usage rollups (no message text, no per-user identity).
    Off by default: returns 404 unless ADMIN_TOKEN is set, and requires a
    matching X-Admin-Token header. Optional `since`/`until` query params
    (ISO-8601) select the window; if omitted, defaults to the last
    ADMIN_USAGE_HOURS hours ending now."""
    if not settings.admin_token:
        raise HTTPException(status_code=404, detail="Not found")
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")

    from medicare_navigator.storage.connection import DuckDBConnection

    now = datetime.now(timezone.utc)
    resolved_since = since if since is not None else now - timedelta(hours=settings.admin_usage_hours)
    resolved_until = until if until is not None else now
    if resolved_since >= resolved_until:
        raise HTTPException(status_code=400, detail="since must be before until")

    columns = [
        "hour_bucket",
        "region",
        "mode",
        "model",
        "sessions_new",
        "requests_total",
        "requests_ok",
        "requests_error",
        "requests_clarification",
        "requests_not_found",
        "requests_limit_reached",
        "prompt_len_short",
        "prompt_len_medium",
        "prompt_len_long",
        "prompt_len_sum",
        "latency_ms_sum",
        "tokens_in_sum",
        "tokens_out_sum",
        "requests_with_tokens",
        "cost_usd_sum",
    ]
    db = DuckDBConnection()
    rows = await asyncio.to_thread(
        db.fetchall,
        # Explicit column list (not SELECT *): ALTER TABLE ADD COLUMN appends new
        # columns to the end of the physical row on disk, which would silently
        # desync a SELECT *-based positional zip() from the `columns` list below
        # on any DB that picked up prompt_len_sum/requests_with_tokens via migration
        # rather than a fresh CREATE TABLE.
        f"SELECT {', '.join(columns)} FROM usage_hourly "
        "WHERE hour_bucket >= ? AND hour_bucket < ? ORDER BY hour_bucket DESC",
        [resolved_since.replace(tzinfo=None), resolved_until.replace(tzinfo=None)],
    )
    return {
        "since": resolved_since.isoformat(),
        "until": resolved_until.isoformat(),
        "default_timezone": settings.default_timezone,
        "rows": [dict(zip(columns, row, strict=True)) for row in rows],
    }


@app.get("/api/privacy")
async def get_privacy_policy():
    return {"text": settings.privacy_policy_text}


class FeedbackRequest(BaseModel):
    message: str
    state: str | None = None
    zip: str | None = None


@app.post("/api/feedback")
async def submit_feedback(req: FeedbackRequest):
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    if len(message) > 2000:
        raise HTTPException(status_code=400, detail="message must be 2000 characters or fewer")

    state = req.state.strip().upper() if req.state else None
    if state and (len(state) != 2 or not state.isalpha()):
        raise HTTPException(status_code=400, detail="state must be a 2-letter code")

    zip_code = req.zip.strip() if req.zip else None
    if zip_code and (len(zip_code) != 5 or not zip_code.isdigit()):
        raise HTTPException(status_code=400, detail="zip must be a 5-digit code")

    from medicare_navigator.feedback.store import append_feedback

    entry = append_feedback(message=message, state=state, zip_code=zip_code)
    return {"status": "ok", "submitted_at": entry["submitted_at"]}


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


@app.post("/api/estimate-batch", response_model=BatchEstimateApiResponse)
async def estimate_batch(req: BatchEstimateRequestPayload):
    plan_id = req.plan_id.strip()
    if not plan_id:
        raise HTTPException(status_code=400, detail="plan_id is required")
    if not req.items:
        raise HTTPException(status_code=400, detail="At least one drug is required")
    if len(req.items) > MAX_BATCH_DRUGS:
        raise HTTPException(
            status_code=400, detail=f"At most {MAX_BATCH_DRUGS} drugs are supported per request"
        )
    for item in req.items:
        if not item.drug.strip():
            raise HTTPException(status_code=400, detail="Each item requires a drug name")

    requests = [
        BatchEstimateRequest(
            plan_key=plan_id,
            drug_name=item.drug.strip(),
            dosage=item.dosage.strip() if item.dosage else None,
            days_supply=req.days_supply,
            ytd_oop_spend=req.ytd_oop_spend,
        )
        for item in req.items
    ]
    results = await run_batch_estimates(requests)

    items: list[BatchEstimateItem] = []
    combined_low = 0.0
    combined_high = 0.0
    any_cost = False
    any_incomplete = False
    for result in results:
        items.append(
            BatchEstimateItem(
                drug=result.request.drug_name,
                data=result.data,
                status=result.status,
                message=result.message,
            )
        )
        if result.status != "ok" or result.data is None:
            any_incomplete = True
            continue
        low, high = _estimate_cost_bounds(result.data)
        if low is None:
            any_incomplete = True
            continue
        combined_low += low
        combined_high += high if high is not None else low
        any_cost = True

    caveat = None
    if any_incomplete:
        caveat = (
            "One or more drugs in this basket could not be totaled (not covered, blocked, or "
            "missing cost-share data) — the combined total below excludes them and may "
            "under-count your actual cost."
        )

    return BatchEstimateApiResponse(
        status="ok",
        items=items,
        combined_total_low=combined_low if any_cost else None,
        combined_total_high=combined_high if any_cost else None,
        caveat=caveat,
    )


@app.post("/api/compare-plans", response_model=PlanComparisonApiResponse)
async def compare_plans(req: ComparePlansRequestPayload):
    drug = req.drug.strip()
    if not drug:
        raise HTTPException(status_code=400, detail="drug is required")
    plan_ids = [p.strip() for p in req.plan_ids if p.strip()]
    if len(plan_ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 plan_ids are required to compare")
    if len(plan_ids) > MAX_COMPARE_PLANS:
        raise HTTPException(
            status_code=400, detail=f"At most {MAX_COMPARE_PLANS} plans are supported per comparison"
        )

    requests = [
        BatchEstimateRequest(
            plan_key=plan_id,
            drug_name=drug,
            dosage=req.dosage.strip() if req.dosage else None,
            days_supply=req.days_supply,
            ytd_oop_spend=req.ytd_oop_spend,
        )
        for plan_id in plan_ids
    ]
    results = await run_batch_estimates(requests)

    items = [
        PlanComparisonItem(
            plan_id=result.request.plan_key,
            data=result.data,
            status=result.status,
            message=result.message,
        )
        for result in results
    ]
    return PlanComparisonApiResponse(status="ok", items=items)


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

    start = time.perf_counter()
    ok = False
    response = None
    try:
        response = await orchestrator.run(message=message, filter_slots=filters, session_id=req.session_id)
        ok = True
    except LLMNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        if settings.analytics_enabled:
            plan_id = req.plan_id or (req.filters.plan_id if req.filters else None)
            usage = response.total_llm_usage if response else None
            collector.record_request(
                prompt_len=len(message or ""),
                ok=ok,
                latency_ms=(time.perf_counter() - start) * 1000,
                region=await _resolve_region(req.region, plan_id, message),
                mode="chat",
                status=response.status if response else "ok",
                tokens_in=usage.input_tokens if usage else 0,
                tokens_out=usage.output_tokens if usage else 0,
                cost_usd=usage.cost_usd if usage else 0.0,
            )
    return response


@app.get("/api/models")
async def list_models():
    from medicare_navigator.llm.models import default_llm_model, list_available_models

    return {"default": default_llm_model(), "models": list_available_models()}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    filters = _filters_to_slots(req.filters, req.message)
    start = time.perf_counter()
    ok = False
    response = None
    try:
        response = await orchestrator.run(
            message=req.message,
            filter_slots=filters,
            session_id=req.session_id,
            llm_model=req.model,
            timezone=req.timezone,
        )
        ok = True
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        if settings.analytics_enabled:
            from medicare_navigator.llm.models import resolve_model

            plan_id = req.filters.plan_id if req.filters else None
            usage = response.total_llm_usage if response else None
            try:
                model_id = resolve_model(req.model).id if ok else "unknown"
            except ValueError:
                model_id = "unknown"
            collector.record_request(
                prompt_len=len(req.message or ""),
                ok=ok,
                latency_ms=(time.perf_counter() - start) * 1000,
                region=await _resolve_region(req.region, plan_id, req.message),
                mode=_resolve_mode(req.mode),
                model=model_id,
                status=response.status if response else "ok",
                tokens_in=usage.input_tokens if usage else 0,
                tokens_out=usage.output_tokens if usage else 0,
                cost_usd=usage.cost_usd if usage else 0.0,
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


def _sync_frontend_dist() -> Path:
    """Copy frontend/src → dist when sources are newer (local dev convenience)."""
    import shutil

    src = settings.project_root / "frontend" / "src"
    dist = settings.project_root / "frontend" / "dist"
    if not src.is_dir():
        return dist

    assets = ("index.html", "app.js", "styles.css", "manifest.json")
    admin_pages = list((src / "admin").glob("*.html")) if (src / "admin").is_dir() else []
    stale = not dist.is_dir()
    if not stale:
        for src_path in (*(src / name for name in assets), *admin_pages):
            dist_path = (
                dist / "admin" / src_path.name if src_path in admin_pages else dist / src_path.name
            )
            if src_path.is_file() and (
                not dist_path.is_file() or src_path.stat().st_mtime > dist_path.stat().st_mtime
            ):
                stale = True
                break

    if stale:
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "icons").mkdir(parents=True, exist_ok=True)
        for name in assets:
            if (src / name).is_file():
                shutil.copy2(src / name, dist / name)
        icons_src = src / "icons"
        if icons_src.is_dir():
            for icon in icons_src.glob("*.png"):
                shutil.copy2(icon, dist / "icons" / icon.name)
        admin_src = src / "admin"
        if admin_src.is_dir():
            (dist / "admin").mkdir(parents=True, exist_ok=True)
            for page in admin_src.glob("*.html"):
                shutil.copy2(page, dist / "admin" / page.name)

    return dist


_frontend = _sync_frontend_dist()
if _frontend.exists():
    _no_cache = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}

    @app.get("/", include_in_schema=False)
    async def serve_index():
        return FileResponse(_frontend / "index.html", media_type="text/html", headers=_no_cache)

    _admin_usage = _frontend / "admin" / "usage.html"
    if _admin_usage.is_file():

        @app.get("/admin/usage", include_in_schema=False)
        async def serve_admin_usage():
            return FileResponse(_admin_usage, media_type="text/html", headers=_no_cache)

    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
