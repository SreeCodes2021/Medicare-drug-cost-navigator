---
name: deployment-agent
description: >-
  Debug Medicare Navigator on Render using Render MCP — deploy status, build/runtime
  logs, metrics, and health checks. Read-only (no env var changes). Use when the user
  invokes /deployment-agent, deployment-agent, or asks to debug Render production,
  deploy failures, ingest cron, or live service logs.
disable-model-invocation: true
---

# Deployment Agent — Render Debug (Read-Only)

User invoked this skill — inspect the **medicare-drug-cost** Render service and correlate findings with this repo's deployment layout.

## Constraints

- **Read-only:** use MCP for discovery, logs, deploys, and metrics only.
- **Do not call** `update_environment_variables` or any write/deploy MCP action.
- **Secrets:** never print `RENDER_API_KEY`, `ANTHROPIC_API_KEY`, or other dashboard secrets in chat.
- **MCP auth:** project config is [`.cursor/mcp.json`](../../mcp.json) → `Authorization: Bearer ${env:RENDER_API_KEY}`. Key must be in local `.env` (not committed).

If `list_services()` fails, verify `RENDER_API_KEY` in `.env`, restart Cursor, retry. See [Render MCP docs](https://render.com/docs/mcp-server).

## Workflow

```
┌──────────────┐    list_services / get_service    ┌─────────────┐
│  Workspace   │ ────────────────────────────────► │ medicare-   │
│  selection   │                                   │ navigator   │
└──────────────┘                                   └──────┬──────┘
                                                          │
     list_deploys ──► list_logs(build) ──► list_logs(error)
                                                          │
     get_metrics ──► optional GET /api/health ──► report
```

Copy this checklist and track progress:

```
Task Progress:
- [ ] Step 1: Confirm MCP + workspace
- [ ] Step 2: list_deploys (latest status)
- [ ] Step 3: list_logs(build) + list_logs(error)
- [ ] Step 4: Check metrics if crash/slow/OOM
- [ ] Step 5: Cross-check repo config + health endpoint
- [ ] Step 6: Report root cause + fix (code or dashboard guidance)
```

### Step 1 — MCP and workspace

1. Call `list_services()` and find **`medicare-drug-cost`** (from [`render.yaml`](../../../render.yaml)).
2. If multiple workspaces, run `list_workspaces()` and set the correct one before continuing.
3. `get_service(serviceId: "<id>")` — note status, URL, plan, disk mount.

### Step 2 — Deploy status

```
list_deploys(serviceId: "<id>", limit: 5)
```

- Latest deploy **failed** → prioritize **build** logs (Step 3).
- **Live** but unhealthy → prioritize **runtime error** logs + metrics.
- **First deploy / empty data** → likely missing initial ingest (see Step 5).

### Step 3 — Logs

**Build / deploy failure:**
```
list_logs(resource: ["<service-id>"], type: ["build"], limit: 200)
```

**Runtime errors:**
```
list_logs(resource: ["<service-id>"], level: ["error"], limit: 100)
```

**Project-specific searches** (adjust terms to the symptom):
```
list_logs(resource: ["<service-id>"], text: ["supercronic"], limit: 50)
list_logs(resource: ["<service-id>"], text: ["medicare-ingest", "spuf"], limit: 50)
list_logs(resource: ["<service-id>"], text: ["DuckDB", "navigator.duckdb"], limit: 50)
list_logs(resource: ["<service-id>"], text: ["uvicorn", "EADDRINUSE"], limit: 50)
list_logs(resource: ["<service-id>"], statusCode: ["502", "503"], limit: 50)
```

### Step 4 — Metrics (when relevant)

```
get_metrics(resourceId: "<id>", metricTypes: ["cpu_usage", "memory_usage", "memory_limit"])
get_metrics(resourceId: "<id>", metricTypes: ["http_request_count"])
```

Use for OOM (exit 137), slow responses, or repeated restarts.

### Step 5 — Project cross-check

Read [project-context.md](project-context.md) for repo-specific failure patterns. Quick anchors:

| Check | Expected |
|-------|----------|
| Service name | `medicare-drug-cost` |
| Disk | `/data` — DuckDB + Chroma |
| Process | `supercronic` + `uvicorn` on `$PORT` |
| Health | `GET /api/health` → `status: ok`, `data_fresh: true` after ingest |
| Dashboard secrets | `ANTHROPIC_API_KEY`, `CORS_ORIGINS` (not in repo) |
| Nightly ingest | `config/deploy.yaml` cron (UTC) via supercronic |

If `data_fresh: false`, guide operator to run initial ingest via Render Shell:
```bash
medicare-ingest spuf --download
```

### Step 6 — Report

Use this template:

```markdown
## Deployment diagnosis — medicare-drug-cost

**Service:** <name> (<id>) — <status>
**Latest deploy:** <status> at <time>

### Findings
- <log/metric evidence with timestamps>

### Root cause
<one sentence>

### Recommended fix
1. <code change in repo OR dashboard action — no MCP writes>
2. <verification step>

### Verify after fix
- `list_deploys` → live
- `list_logs(level: error)` → no new errors
- `GET /api/health` → `data_fresh: true`, `llm_configured: true`
```

## Common patterns (this project)

| Symptom | Likely cause | Fix direction |
|---------|--------------|---------------|
| Build fails on Docker | Dockerfile / deps | Check build logs; [`Dockerfile`](../../../Dockerfile), [`pyproject.toml`](../../../pyproject.toml) |
| Service starts, 502 | uvicorn not on `$PORT` | [`scripts/docker-start.sh`](../../../scripts/docker-start.sh) uses `PORT` |
| Health ok, empty plans | No SPUF ingest | Shell: `medicare-ingest spuf --download` |
| Stale `data_fresh` | supercronic / cron | [`config/deploy.yaml`](../../../config/deploy.yaml), ingest logs |
| CORS errors in browser | Wrong `CORS_ORIGINS` | Dashboard secret must match `https://<app>.onrender.com` |
| LLM fallback only | Missing API key | Dashboard: `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` |

## MCP quick reference (read-only)

```
list_workspaces()
get_selected_workspace()
list_services()
get_service(serviceId: "<id>")
list_deploys(serviceId: "<id>", limit: 5)
list_logs(resource: ["<id>"], type: ["build"], limit: 200)
list_logs(resource: ["<id>"], level: ["error"], limit: 100)
get_metrics(resourceId: "<id>", metricTypes: ["cpu_usage", "memory_usage", "memory_limit"])
```

## Related repo docs

- [docs/deployment.md](../../../docs/deployment.md)
- [render.yaml](../../../render.yaml)
- [project-context.md](project-context.md)
