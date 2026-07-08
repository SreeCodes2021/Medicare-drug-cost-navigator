# Medicare Drug Cost & Benefit-Transparency Navigator

A Phase 6 system that computes the out-of-pocket cost of a specific prescription drug on a specific Medicare Part D / MA-PD plan from official CMS quarterly data — with every dollar figure traceable to a source record, and honest hard stops when the data can't support a reliable estimate. See [docs/business-solution.md](docs/business-solution.md) for the full problem statement and roadmap.

## Features

- **Chat-first UI** (plus a guided estimate form) with optional filters for drug, plan, dosage, and YTD spend
- **`estimate_drug_cost`** — one consolidated, deterministic 8-step pipeline (plan resolution → drug normalization → formulary → days-supply mapping → benefit-phase → pricing/cost-share → output) that handles six named CMS data-correctness rules explicitly (see [docs/navigator-implementation-spec.md](docs/navigator-implementation-spec.md))
- **Single Navigator agent** — one LLM tool-calling loop over 3 MCP tools (`estimate_drug_cost`, `lookup_plan`, `list_plans`); the LLM explains results in plain English but never computes a dollar amount itself
- **Mandatory guardrails** — every `$` figure in the LLM's answer is checked against the tool's `cost_low`/`cost_high`; safety-critical caveats are force-appended verbatim if the LLM drops or paraphrases them
- **DuckDB** embedded store for CMS SPUF data (plans, formulary, pricing, cost-share) — no external database or vector store required
- **Fixed disclaimer banner** always visible on screen

## Quick start

### Prerequisites

- Python 3.11+
- (Optional) Anthropic or OpenAI API key for full LLM agent responses

### Setup

```bash
cd Medicare-drug-cost-navigator
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Edit `.env`: set `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY` with `LLM_PROVIDER=openai`) for real LLM responses, **or** set `LLM_MOCK=1` for an offline, deterministic stand-in (no API key required — used by the test suite).

### Load CMS data (local)

```bash
# Offline tests use tests/fixtures/spuf/ — for local API with real FL data:
medicare-ingest spuf --download --states FL --merge-states

# Or ingest the offline fixture (fast, no network):
medicare-ingest spuf --source tests/fixtures/spuf
```

`--states FL --merge-states` matches `config/ingest_filters.yaml` (Florida only, verified against real CMS data) and avoids loading the full multi-GB national file. See [docs/developer-guide.md](docs/developer-guide.md#5-data-layer) for real ingested row counts.

### Build frontend (local dev)

```bash
scripts/build-frontend.sh
```

Static UI sources live in `frontend/src/`; the script copies them to `frontend/dist/` (gitignored). Docker/Render builds `dist` automatically in the image.

### Run API server

```bash
uvicorn medicare_navigator.api.app:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 — the UI is served from `frontend/dist/`.

### Run tests

```bash
pytest
```

### Run evaluation suite

```bash
medicare-eval
# or: python -m medicare_navigator.eval.run_eval
```

## Configuration

| Variable | Description | Default |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` or `openai` | `anthropic` |
| `LLM_MODEL` | Model name | `claude-sonnet-4-6` |
| `ANTHROPIC_API_KEY` | Claude API key | — |
| `OPENAI_API_KEY` | OpenAI API key | — |
| `LLM_MOCK` | `1` for offline deterministic mock agent (no API key needed) | `0` |
| `DUCKDB_PATH` | DuckDB file path | `./data/navigator.duckdb` |
| `MAX_CHAT_TURNS` | Follow-up limit per session | `5` |

Without a configured API key **and** without `LLM_MOCK=1`, `/api/health` reports `degraded` (HTTP 503) and `/api/chat` returns HTTP 503 — there is no silent fallback. This is intentional: an earlier heuristic fallback that answered without a real LLM was removed so that every non-mock answer is either a real model response or an explicit, visible failure. See the full list of environment variables in [docs/developer-guide.md](docs/developer-guide.md#12-configuration).

## API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check (includes `data_fresh`, `seeded_at`) |
| `GET` | `/api/disclaimer` | Canonical disclaimer text |
| `GET` | `/api/meta/as-of` | Data freshness manifest |
| `GET` | `/api/plans` | Plan list (from SPUF-loaded DuckDB) |
| `POST` | `/api/query` | Structured query |
| `POST` | `/api/chat` | Conversational turn |

## Project structure

```
src/medicare_navigator/
├── agent/          # Navigator agent (LLM tool-calling loop) + system prompt
├── api/             # FastAPI app and HTTP endpoints
├── eval/            # Offline evaluation suite (queries.jsonl, run_eval.py)
├── guardrails/      # Citation building + verbatim-caveat / dollar-traceability enforcement
├── ingestion/       # CMS SPUF download, parsing, and DuckDB schema
├── llm/             # Provider-agnostic LLM client (Anthropic / OpenAI / mock)
├── mcp/             # Tool schemas + in-process MCP tool registry
├── models/          # Pydantic request/response models
├── orchestrator/    # Thin router into the Navigator agent
├── qa/               # CLI for manually driving /api/chat
├── session/          # In-memory session/turn-limit tracking
├── storage/          # DuckDB connection + repository classes
├── tools/            # estimate_drug_cost (8-step pipeline), lookup_plan, normalize_drug
└── ui_test/           # Automated frontend/API contract checks
frontend/src/       # static chat + guided-estimate UI sources (built to frontend/dist/)
config/             # ingest filters, deploy schedule, disclaimer text
docs/               # architecture, business case, implementation history
```

## Documentation

Start at **[docs/README.md](docs/README.md)** for the full documentation index. Highlights:

- [Developer Guide](docs/developer-guide.md) — stack, architecture, setup, testing, deployment, API reference
- [Business Solution](docs/business-solution.md) — problem statement, capabilities, roadmap
- [Navigator Implementation Spec](docs/navigator-implementation-spec.md) — the 8-step cost pipeline and the six CMS data-correctness rules it handles
- [Phase 6 Implementation Plan](docs/phase-6-implementation-plan.md) — what shipped in the current release and why
- [Deployment](docs/deployment.md) — Render, cron ingest, persistent disk
- [Build Requirements](build-requirements.md) — long-term product vision (broader than the current release)

## Deploy to Render

1. Push this repo to GitHub.
2. [Render](https://render.com) → **New Blueprint** → connect repo (`render.yaml`).
3. Set secrets: `ANTHROPIC_API_KEY`, `CORS_ORIGINS=https://<your-app>.onrender.com`.
4. After first deploy, **Shell** on the web service:

```bash
medicare-ingest spuf --download --states FL --merge-states
```

5. Verify `GET /api/health` → `data_fresh: true`.

Nightly ingest: supercronic reads schedule from [`config/deploy.yaml`](config/deploy.yaml). Resources: [`render.yaml`](render.yaml). Full detail in [docs/deployment.md](docs/deployment.md).

## Data scope

Formulary, pricing, and cost-share data come from **CMS SPUF**, currently ingested for **Florida only** (`config/ingest_filters.yaml`). The ingestion pipeline already reads the full national CMS file and filters by state, so expanding coverage is primarily a matter of adding states to the config and scaling storage/ingest scheduling accordingly — see [docs/business-solution.md § National multi-state ingest](docs/business-solution.md#76-national-multi-state-ingest) for the specific remaining work (disk sizing, sequential state merges).

## Disclaimer

Disclaimer: This tool is for informational purposes only. The model can make mistakes. This is not medical advice, financial advice, or insurance enrollment guidance. Costs shown are an estimate based on CMS-published plan data for the current quarter, not a guarantee of actual pharmacy charge or real-time pricing. Confirm any information with your doctor, pharmacist, or Medicare plan before making decisions.

*(Canonical source: [`config/disclaimer.txt`](config/disclaimer.txt), served at `GET /api/disclaimer`.)*
