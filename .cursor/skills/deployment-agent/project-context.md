# Medicare Navigator — Render project context

Reference for `/deployment-agent`. Read when correlating MCP output with this repo.

## Service topology

Single **Docker web service** (`medicare-navigator`):

- **Image entry:** [`scripts/docker-start.sh`](../../../scripts/docker-start.sh)
- **API:** `uvicorn medicare_navigator.api.app:app --host 0.0.0.0 --port $PORT`
- **Scheduler:** supercronic runs cron from [`scripts/generate-crontab.py`](../../../scripts/generate-crontab.py) ← [`config/deploy.yaml`](../../../config/deploy.yaml)
- **Nightly job:** [`scripts/run-daily-ingest.sh`](../../../scripts/run-daily-ingest.sh) → `medicare-ingest spuf --download --preserve-other`

## Persistent disk (`/data`)

| Path | Purpose |
|------|---------|
| `/data/navigator.duckdb` | Plan/formulary data (DuckDB) |
| `/data/chroma` | Policy retrieval index |
| `/data/manifest.json` | Ingest freshness metadata |

Env defaults in [`render.yaml`](../../../render.yaml): `DATA_DIR`, `DUCKDB_PATH`, `CHROMA_PATH`.

Render cron jobs **cannot** mount this disk — ingest must run in-container via supercronic.

## Health endpoint

`GET /api/health` returns:

- `status`: `"ok"` when API is up
- `data_fresh` / manifest fields from `data_freshness_summary()`
- `llm_configured`: whether Anthropic/OpenAI key is set
- `navigator_mode`: typically `mcp_agent`

Production URL: `https://<medicare-navigator>.onrender.com/api/health`

## Secrets (Render dashboard only)

Set in Blueprint/dashboard (`sync: false` in render.yaml):

- `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`
- `CORS_ORIGINS` — must include production origin

`RENDER_API_KEY` is for **local Cursor MCP only** — do not add to the Render service env.

## First-deploy checklist

1. Blueprint deploy succeeds (build logs clean).
2. Render Shell: `medicare-ingest spuf --download` (one-time seed).
3. `/api/health` → `data_fresh: true`.
4. Chat/UI smoke against production URL.

## Log grep hints

| Log substring | Meaning |
|---------------|---------|
| `supercronic` | Cron scheduler started or failed |
| `medicare-ingest` / `spuf` | Nightly or manual ingest |
| `duckdb` / `navigator.duckdb` | DB open/write errors |
| `chroma` | Vector store path issues |
| `CMS` / `data.cms.gov` | Download failures during ingest |
| `CORS` | Origin mismatch (runtime, not always in Render logs) |

## Scaling / resources

Operator settings in [`config/deploy.yaml`](../../../config/deploy.yaml) (`render.plan`, `render.disk_size_gb`) mirror [`render.yaml`](../../../render.yaml) `plan` and `disk.sizeGB`. Change in repo + push; MCP cannot modify plan/disk.
