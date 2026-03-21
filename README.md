# Medicare Drug Cost & Benefit-Transparency Navigator

A Phase 1 demo system that explains Medicare Part D / MA drug costs and benefit transparency using public government data, deterministic tools, and in-repo LLM agents.

## Features

- **Chat-first UI** with optional filters for drug, plan, dosage, and YTD spend
- **5 deterministic tools**: drug normalization, formulary/benefit lookup, cost trends, alternatives, policy retrieval
- **3 LLM agents**: intake (NLU), policy explanation, synthesis with citations
- **Hand-rolled orchestrator** with conditional routing and session-scoped follow-ups (max 5 turns)
- **DuckDB** embedded store + Chroma vector store for policy corpus
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

### Load CMS data (local)

```bash
# Offline tests use tests/fixtures/spuf/ — for local API with real FL+TX data:
medicare-ingest spuf --download

# Or ingest the offline fixture:
medicare-ingest spuf --source tests/fixtures/spuf
```

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
| `DUCKDB_PATH` | DuckDB file path | `./data/navigator.duckdb` |
| `MAX_CHAT_TURNS` | Follow-up limit per session | `5` |

Without API keys, the system uses deterministic fallbacks for agent outputs while tools remain fully functional.

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
├── agents/         # policy, synthesis LLM agents
├── intake/         # InputMerger + intake NLU agent
├── tools/          # 5 deterministic tools
├── orchestrator/   # hand-rolled pipeline
├── storage/        # DuckDB repositories
├── ingestion/      # CMS SPUF ingest CLI
├── api/            # FastAPI app
└── eval/           # evaluation suite
frontend/src/       # static chat UI sources (built to frontend/dist/)
config/             # ingest filters, benefit params, deploy settings, disclaimer
docs/               # implementation plan, data sources
```

## Documentation

- [Phase 1 Implementation Plan](docs/phase-1-implementation-plan.md)
- [Phase 2 Implementation Plan](docs/phase-2-implementation-plan.md)
- [Phase 3 Implementation Plan](docs/phase-3-implementation-plan.md)
- [Phase 4 Implementation Plan](docs/phase-4-implementation-plan.md)
- [Deployment & scheduled ingest](docs/deployment.md) (includes Render)
- [Data Sources](docs/data-sources.md)
- [Build Requirements](build-requirements.md)

## Deploy to Render

1. Push this repo to GitHub.
2. [Render](https://render.com) → **New Blueprint** → connect repo (`render.yaml`).
3. Set secrets: `ANTHROPIC_API_KEY`, `CORS_ORIGINS=https://<your-app>.onrender.com`.
4. After first deploy, **Shell** on the web service:

```bash
medicare-ingest spuf --download
```

5. Verify `GET /api/health` → `data_fresh: true`.

Nightly ingest: supercronic reads schedule from [`config/deploy.yaml`](config/deploy.yaml). Resources: [`render.yaml`](render.yaml).

## Data scope

Production formulary data comes from **CMS SPUF** (FL + TX per `config/ingest_filters.yaml`). Cost trends, alternatives, and policy corpus return `no_match` until real loaders are added.

## Disclaimer

This tool is for informational purposes only. The model can make mistakes. This is not medical advice, financial advice, or insurance enrollment guidance. Confirm any information with your doctor, pharmacist, or Medicare plan before making decisions.
