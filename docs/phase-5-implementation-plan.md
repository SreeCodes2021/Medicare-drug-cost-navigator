# Phase 5 Implementation Plan

**Medicare Drug Cost & Benefit-Transparency Navigator**

This document records what was built in Phase 5 on top of [phase-4-implementation-plan.md](./phase-4-implementation-plan.md). The functional specification remains [build-requirements.md](../build-requirements.md).

**Commit range:** `b32a952` (last Phase 4 commit) → `e7be480` (HEAD at time of writing). Unstaged / untracked work is excluded.

---

## 1. Overview

Phase 5 makes the Render deployment path production-ready: the chat UI is built from committed `frontend/src/` inside Docker, SPUF ingest can merge states incrementally for low-memory Starter instances, bulk inserts are batched with stderr progress logging, DuckDB index drops fix state purges, the API ensures schema on startup, and a read-only Render deployment-agent skill supports live debugging via MCP.

**Phase 5 scope:** multi-stage Docker frontend build; static chat UI in `frontend/src/`; `scripts/build-frontend.sh` for local dev; `--merge-states` SPUF ingest; `_purge_states` with index drop/recreate; batched `executemany` inserts; ingest progress logging; `ensure_schema()` FastAPI lifespan hook; `PROJECT_ROOT` resolution for pip-installed Docker layout; Render deployment-agent Cursor skill + MCP config; expanded `test_spuf_ingest.py` coverage.

**Explicitly unchanged in Phase 5:** national plan coverage beyond configured states; real CMS cost-trend, Orange Book, and policy-corpus loaders; live tier-change detection across plan years; CI eval gate; npm/bundler frontend pipeline.

---

## 2. Decisions locked for Phase 5

| Decision | Choice | Rationale |
|---|---|---|
| Frontend source of truth | **`frontend/src/`** (HTML/CSS/JS) | Version-controlled UI; no committed `frontend/dist` |
| Docker frontend build | **Alpine copy stage → `frontend/dist`** | Render image always ships UI matching `src/` |
| Local dist | **`scripts/build-frontend.sh`** copies `src/` → `dist/` | Parity with Docker; pytest autouse builds dist if missing |
| Low-memory first ingest | **`--merge-states` per state** | Starter plan OOM on full FL+TX reload; merge one state at a time |
| State replacement | **`_purge_states` deletes plans + child rows for target states** | Re-run same state replaces stale rows without wiping other states |
| DuckDB bulk delete | **Drop SPUF indexes before purge, recreate after ingest** | DuckDB ART index bug: `DELETE` fails on indexed tables at scale |
| Bulk inserts | **10-part `executemany` batches** (`_WRITE_PARTS`) | Reduces peak memory vs row-by-row `execute` |
| Ingest observability | **UTC timestamped stderr progress** every 500k formulary scan rows | Render Shell / logs show long-running ingest status |
| API cold start | **`ensure_schema()` in FastAPI lifespan** | Empty disk gets tables/indexes before first request |
| Docker config paths | **`PROJECT_ROOT=/app` env** + `_resolve_project_root()` | Pip install layout differs from src checkout; `config/` must resolve |
| Production debugging | **Read-only deployment-agent skill + Render MCP** | Correlate deploy logs, metrics, and `/api/health` without dashboard writes |
| Nightly ingest default | **Still `--preserve-other`** (unchanged) | Full refresh of configured states; merge-states is manual/ops pattern |

---

## 3. Deployment architecture

### 3.1 Docker build flow (updated)

```mermaid
flowchart LR
    Src[frontend/src]
    Builder[alpine frontend-builder]
    Dist[frontend/dist]
    Py[python:3.11-slim]
    API[uvicorn + supercronic]

    Src --> Builder
    Builder --> Dist
    Dist --> Py
    Py --> API
```

Phase 4 copied a pre-built `frontend/dist` into the image. Phase 5 builds `dist` in a multi-stage Dockerfile so Render deploys never depend on a local-only dist commit.

### 3.2 Low-memory ingest pattern (Render Starter)

Full `medicare-ingest spuf --download` for FL + TX can exceed Starter memory. Phase 5 documents and implements incremental loading:

```bash
medicare-ingest spuf --download --states FL --merge-states
medicare-ingest spuf --download --states TX --merge-states
```

Each run downloads and scans the national CMS zip but writes only the selected state's plans. Manifest `spuf.states` accumulates (`["FL"]` → `["FL", "TX"]`).

### 3.3 New / updated deployment assets

| Asset | Role |
|---|---|
| `Dockerfile` | Multi-stage: Alpine copies `frontend/src` → `dist`; sets `PROJECT_ROOT=/app` |
| `scripts/build-frontend.sh` | Local copy of `src/` → `dist/` for dev and tests |
| `render.yaml` | Adds `PROJECT_ROOT=/app` env var |
| `.cursor/skills/deployment-agent/SKILL.md` | Read-only Render MCP workflow for production debug |
| `.cursor/skills/deployment-agent/project-context.md` | Service topology, health, log grep hints |
| `.cursor/mcp.json` | Render MCP server config (`RENDER_API_KEY` from local `.env`) |
| `.env.example` | Documents `RENDER_API_KEY` (local Cursor only) |
| `docs/deployment.md` | Multi-stage Docker note; low-memory `--merge-states` section |

### 3.4 Environment

Added in Phase 5:

```bash
PROJECT_ROOT=/app    # Docker / Render (repo root with config/)
RENDER_API_KEY=      # local Cursor MCP only — not on Render service
```

Existing production vars unchanged (`DATA_DIR`, `DUCKDB_PATH`, `CHROMA_PATH`, `PORT`, `CORS_ORIGINS`).

---

## 4. Frontend (committed chat UI)

Phase 5 adds the first version-controlled static UI under `frontend/src/`:

| File | Purpose |
|---|---|
| `index.html` | Three-column layout: filters, chat, results panel |
| `styles.css` | Responsive layout, cards, citation details, benefit-phase pills |
| `app.js` | Chat session, filter payload, results baseline merge, citation linkify |

**UI capabilities (committed at `e7be480`):**

- Disclaimer banner from `GET /api/disclaimer`
- Filter panel: drug, dosage, plan (populated from `GET /api/plans`), contract year, YTD OOP, include alternatives/trend toggles
- Chat with 5-turn counter, prompt chips, loading state
- Results panel: formulary/cost-share card, cost-trend bars, alternatives list, expandable citations with source URLs
- **Baseline merge:** follow-up turns preserve prior formulary/trend/alternatives/citations unless drug changes
- Citation refs in assistant messages link to citation `<details>` in results panel

**Not in committed Phase 5** (unstaged WIP): plan-load polling while ingest runs, Refresh plans button, fixture-aligned prompt chips.

---

## 5. Ingestion changes

### 5.1 `--merge-states` mode

New CLI flag on `medicare-ingest spuf`:

```bash
medicare-ingest spuf --source path --merge-states
medicare-ingest spuf --download --states FL --merge-states
```

Behavior:

1. `create_tables(conn, drop_existing=False)` — keep supplemental tables
2. `_purge_states(conn, filters.states)` — remove existing plans + formulary/beneficiary_cost/pricing rows for those states
3. Load and insert new rows for filtered states only
4. `create_indexes(conn)` — recreate indexes after bulk writes
5. Manifest merges `spuf.states` with any states already recorded

Mutually exclusive with full replace: without `--merge-states`, `--preserve-other` still drops and recreates all SPUF tables (Phase 4 behavior).

### 5.2 Purge + index handling

`ingestion/schema.py` adds:

- `SPUF_INDEX_NAMES` tuple
- `drop_spuf_indexes(conn)` — drop before bulk `DELETE`
- `ensure_schema(db)` — `create_tables(drop_existing=False)` + `create_indexes`

`_purge_states` in `spuf.py` calls `drop_spuf_indexes` before deleting child rows, fixing DuckDB failures when purging large formulary sets on indexed tables.

### 5.3 Batched inserts and progress logging

Replaced per-row `INSERT` loops with:

- `_insert_in_parts()` — `executemany` in 10 batches with progress lines
- `_count_formulary_insert_rows` / `_iter_formulary_insert_rows` — two-pass formulary write
- `_count_pricing_rows` / `_iter_pricing_insert_rows` — two-pass pricing write (count then insert)
- Beneficiary cost rows collected in memory, then batch-inserted

Progress logs to **stderr** with UTC timestamps and file labels, e.g.:

```
[2026-05-17 22:30:00 UTC] [basic drugs formulary file 2026] scanned 500,000 rows, kept 12,345 for selected plans
[2026-05-17 22:35:00 UTC] [formulary] wrote part 3/10 (150,000/500,000 rows)
```

Stats dict now includes `plans_purged` and `total_plans` (DB-wide plan count after ingest).

### 5.4 CLI output

`medicare-ingest spuf` completion line reports loaded vs total plans and manifest states:

```
SPUF ingestion complete: 2 plans loaded (3 total in DB, 45000 formulary rows).
Manifest as_of: 2026-01-15 (source_id=spuf_2026_q1, states=['FL', 'TX'])
```

---

## 6. API startup

`api/app.py` registers a FastAPI **lifespan** handler that calls `ensure_schema()` before serving requests. This creates empty SPUF/supplemental tables and indexes on a fresh `/data` disk without requiring a manual ingest first (health will still report `data_fresh: false` until ingest completes).

---

## 7. Config resolution

`config.py` adds `_resolve_project_root()`:

1. `PROJECT_ROOT` env if set (Docker)
2. Src layout: parent of `src/medicare_navigator/`
3. Walk `cwd` and parents for `config/ingest_filters.yaml`

Fixes ingest filter and config path resolution when the package is pip-installed at `/app` rather than run from a git checkout.

---

## 8. Test coverage changes

| Change | Covers |
|---|---|
| `conftest.py` `ensure_frontend_dist` (session autouse) | Runs `build-frontend.sh` if `frontend/dist/` missing |
| `test_ingest_spuf_merge_states_fl_then_tx` | FL then TX merge accumulates 3 plans, manifest states |
| `test_ingest_spuf_merge_states_replaces_same_state` | Re-ingest same state purges and reloads |
| `test_purge_states_with_indexes_and_many_formulary_rows` | 6k formulary rows + indexes; FL purge keeps TX |
| `test_ui.py` `offline_getter` | Uses `patch_settings` + SPUF fixture for in-process UI checks |
| `ui_test/checks.py` smoke messages | Plan keys updated to fixture `H8888-001` |

Run offline suite:

```bash
scripts/build-frontend.sh   # optional; conftest runs it if needed
pytest tests/ -v
```

---

## 9. Repo layout (Phase 5 additions / changes)

```
frontend/src/                        # new — committed UI sources
  ├── index.html
  ├── app.js
  └── styles.css
scripts/build-frontend.sh            # new

Dockerfile                           # multi-stage frontend build; PROJECT_ROOT
render.yaml                          # PROJECT_ROOT env
.env.example                         # RENDER_API_KEY note

.cursor/
├── mcp.json                         # new
└── skills/deployment-agent/         # new
    ├── SKILL.md
    └── project-context.md

src/medicare_navigator/
├── api/app.py                       # lifespan → ensure_schema
├── config.py                        # _resolve_project_root
├── ingestion/
│   ├── cli.py                       # --merge-states
│   ├── schema.py                    # drop_spuf_indexes, ensure_schema
│   └── spuf.py                      # purge, batch insert, progress logging
└── ui_test/checks.py                # fixture plan keys

tests/
├── conftest.py                      # ensure_frontend_dist
└── test_spuf_ingest.py              # merge-states + purge tests

docs/
├── phase-5-implementation-plan.md   # new
└── deployment.md                    # low-memory ingest, Docker stages
```

---

## 10. How to run

```bash
# Build frontend for local dev / tests
scripts/build-frontend.sh

# Local — offline fixture
medicare-ingest spuf --source tests/fixtures/spuf
uvicorn medicare_navigator.api.app:app --reload --port 8000

# Local — real CMS data, one state at a time (low memory)
medicare-ingest spuf --download --states FL --merge-states
medicare-ingest spuf --download --states TX --merge-states

# Local — full replace (all states in ingest_filters.yaml)
medicare-ingest spuf --download

# Tests
pytest tests/ -v

# Docker (builds frontend inside image)
docker build -t medicare-navigator .
docker run -p 8000:8000 -v medicare-data:/data \
  -e ANTHROPIC_API_KEY=sk-... medicare-navigator

# Render production debug (Cursor)
# Set RENDER_API_KEY in .env, invoke /deployment-agent skill
```

---

## 11. Commits in Phase 5

| Commit | Summary |
|---|---|
| `db22c7d` | Build frontend in Docker from committed `frontend/src`; `build-frontend.sh`; pytest dist bootstrap |
| `9e1de80` | `--merge-states` SPUF ingest; `PROJECT_ROOT`; deployment-agent skill; deployment doc |
| `ee84a68` | `ensure_schema()` on FastAPI lifespan startup |
| `ac121b2` | UTC stderr progress logging during SPUF ingest |
| `e7be480` | Drop indexes before state purge; batched bulk inserts; purge/index tests |

---

## 12. Phase 5 → Phase 6 (deferred)

Not in Phase 5 (committed scope):

- **National plan coverage** — expand `config/ingest_filters.yaml` and automate multi-state merge beyond FL + TX
- **Real supplemental loaders** — CMS Part D spending (cost trends), FDA Orange Book (alternatives), policy corpus → Chroma
- **Live tier-change detection** across plan years with `tier_change_evidence` artifacts
- **CI eval gate** — `.github/workflows` running `pytest` + `medicare-eval` on PRs
- **Frontend bundler / asset pipeline** — minification, cache-busting build step (current copy-is-build)
- **Ingest plan polling UX** — unstaged work: poll `/api/plans` during background ingest, Refresh button

See [build-requirements.md](../build-requirements.md) Section 9 for acceptance criteria these items satisfy.
