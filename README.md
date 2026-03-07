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

### Seed demo data

```bash
medicare-ingest
# or: python -m medicare_navigator.ingestion.cli
```

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
| `GET` | `/api/plans` | Demo plan list |
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
├── ingestion/      # seed data CLI
├── api/            # FastAPI app
└── eval/           # evaluation suite
frontend/dist/      # static chat UI
config/             # demo plans, benefit params, disclaimer
docs/               # implementation plan, data sources
```

## Documentation

- [Phase 1 Implementation Plan](docs/phase-1-implementation-plan.md)
- [Phase 2 Implementation Plan](docs/phase-2-implementation-plan.md)
- [Phase 3 Implementation Plan](docs/phase-3-implementation-plan.md)
- [Deployment & scheduled ingest](docs/deployment.md)
- [Data Sources](docs/data-sources.md)
- [Build Requirements](build-requirements.md)

## Demo plans

~12 representative Part D and MA-PD plans are configured in `config/demo_plans.yaml`. Seed data includes formulary entries, cost trends, and alternatives for common demo drugs (metformin, lisinopril, atorvastatin, eliquis, etc.).

## Disclaimer

This tool is for informational purposes only. The model can make mistakes. This is not medical advice, financial advice, or insurance enrollment guidance. Confirm any information with your doctor, pharmacist, or Medicare plan before making decisions.
